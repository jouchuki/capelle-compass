"""
Chat handler — session management and message submission.

Exposes endpoints for creating sessions, listing sessions, sending
messages, and retrieving chat history.  When a user sends a message,
the handler creates a placeholder assistant message and publishes
a job to the queue for the ohrs worker to fill in.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response

from capelle_platform.auth.base_authenticator import BaseAuthenticator
from capelle_platform.models.job import JobMessage
from capelle_platform.models.message import (
    ChatMessage,
    ChatMessageCreate,
    MessageRole,
    MessageStatus,
)
from capelle_platform.models.session import ChatSession, ChatSessionCreate
from capelle_platform.models.user import User
from capelle_platform.observability import get_logger
from capelle_platform.observability.logger import TraceContext
from capelle_platform.queue.base_queue import BasePublisher
from capelle_platform.quota.base_enforcer import BaseQuotaEnforcer, QuotaExceededError
from capelle_platform.settings import Settings
from capelle_platform.store.base_store import BaseStore
from capelle_platform.telemetry.base_recorder import BaseTelemetryRecorder
from capelle_platform.utils import generate_id
from capelle_platform.models.mode import Mode
from capelle_platform.web.host import (
    Domain,
    domain_for_host,
)

_logger = get_logger(__name__)

class ChatHandler:
    """
    HTTP handler for chat sessions and messages.

    Separates the decision to run an analysis (this handler) from
    the actual execution (worker pool + OhrsExecutor).
    """

    def __init__(
        self,
        store: BaseStore,
        publisher: BasePublisher,
        authenticator: BaseAuthenticator,
        settings: Settings,
        quota: BaseQuotaEnforcer,
        recorder: BaseTelemetryRecorder,
    ) -> None:
        """
        Construct with injected dependencies.

        Args:
            store: Persistence layer for sessions and messages.
            publisher: Queue publisher to dispatch ohrs jobs.
            authenticator: Token validator for extracting the current user.
            settings: Application settings (used for public origin, etc.).
            quota: Daily-analysis quota enforcer. ``send_message`` denies
                submissions for users who already hit today's cap.
            recorder: Telemetry sink for session/message/share events.
        """
        self._store = store
        self._publisher = publisher
        self._auth = authenticator
        self._settings = settings
        self._quota = quota
        self._recorder = recorder
        self.router = APIRouter(tags=["chat"])

        self.router.add_api_route(
            "/api/chat/sessions", self.create_session, methods=["POST"], status_code=201
        )
        self.router.add_api_route(
            "/api/chat/sessions", self.list_sessions, methods=["GET"]
        )
        self.router.add_api_route(
            "/api/chat/sessions/{session_id}", self.get_session, methods=["GET"]
        )
        self.router.add_api_route(
            "/api/chat/sessions/{session_id}",
            self.delete_session,
            methods=["DELETE"],
            status_code=204,
        )
        self.router.add_api_route(
            "/api/chat/sessions/{session_id}/messages",
            self.send_message,
            methods=["POST"],
            status_code=202,
        )
        self.router.add_api_route(
            "/api/chat/sessions/{session_id}/messages",
            self.list_messages,
            methods=["GET"],
        )
        self.router.add_api_route(
            "/api/chat/search", self.search_messages, methods=["GET"]
        )
        # --- Fork links (Wave 3 C2) ---
        self.router.add_api_route(
            "/api/chat/sessions/{session_id}/fork-link",
            self.create_fork_link,
            methods=["POST"],
            status_code=201,
        )
        # The fork view endpoint is the ONLY chat route that does not
        # require authentication. The token itself is the capability —
        # see ``resolve_fork_token`` for expiration enforcement.
        self.router.add_api_route(
            "/api/chat/fork/{token}",
            self.get_fork_view,
            methods=["GET"],
        )
        self.router.add_api_route(
            "/api/chat/fork/{token}/adopt",
            self.adopt_fork,
            methods=["POST"],
            status_code=201,
        )

    def _get_current_user(self, request: Request) -> User:
        """
        Extract and validate the user from cookie or Authorization header.

        The HttpOnly auth cookie is the primary credential for the web
        frontend (not reachable from JS, so XSS can't steal it). We still
        accept a bearer header for programmatic clients and tests.
        Raises 401 if neither is present or the token is invalid.
        """
        token = request.cookies.get(self._settings.auth_cookie_name)
        if not token:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
        if not token:
            raise HTTPException(status_code=401, detail="Missing authorization")
        try:
            return self._auth.validate_token(token)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @staticmethod
    def _request_host(request: Request) -> str:
        """Return the request's Host header (empty string when absent)."""
        return request.headers.get("host", "")

    async def create_session(
        self, payload: ChatSessionCreate, request: Request
    ) -> dict[str, Any]:
        """Create a new chat session for the authenticated user.

        The ``mode`` field on the payload determines which analysis flow the
        executor runs for every message in this session. Pre-mode clients
        that omit the field land on ``DEFAULT_MODE`` via the pydantic
        default — backward compatible with no extra branching here.

        """
        user = self._get_current_user(request)
        host = self._request_host(request)
        domain = domain_for_host(host)

        session = ChatSession(
            id=generate_id(),
            user_id=user.id,
            title=payload.title,
            mode=payload.mode,
            domain=domain,
        )
        await self._store.create_session(session)
        _logger.info(
            "session_created",
            session_id=session.id,
            user_id=user.id,
            mode=session.mode,
            domain=session.domain,
        )
        await self._recorder.record(
            user_id=user.id,
            event_type="session_created",
            session_id=session.id,
            properties={"mode": session.mode, "domain": session.domain},
        )
        return session.model_dump()

    async def list_sessions(self, request: Request) -> dict[str, Any]:
        """List the authenticated user's sessions for the requesting domain.

        """
        user = self._get_current_user(request)
        domain: Domain = domain_for_host(self._request_host(request))
        sessions = await self._store.list_sessions(user.id, domain=domain)
        return {
            "sessions": [s.model_dump() for s in sessions],
            "total": len(sessions),
        }

    async def get_session(self, session_id: str, request: Request) -> dict[str, Any]:
        """Fetch a session with its full message history."""
        user = self._get_current_user(request)
        session = await self._store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.user_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")
        if session.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Session not found")
        messages = await self._store.list_messages(session_id)
        return {
            **session.model_dump(),
            "messages": [m.model_dump() for m in messages],
        }

    async def delete_session(
        self, session_id: str, request: Request
    ) -> Response:
        """
        Soft-delete a session for the authenticated caller.

        The row stays in the database with ``deleted_at`` set, but the
        session disappears from user-facing listings and its GET route
        starts returning 404. This is reversible by an admin; it is
        NOT a hard delete. Returns 204 No Content; re-deleting is a
        no-op (still 204).
        """
        user = self._get_current_user(request)
        session = await self._store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.user_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")
        await self._store.soft_delete_session(session_id)
        await self._recorder.record(
            user_id=user.id,
            event_type="session_deleted",
            session_id=session_id,
            properties={"title": session.title[:120]},
        )
        _logger.info(
            "session_deleted",
            session_id=session_id,
            user_id=user.id,
        )
        return Response(status_code=204)

    async def send_message(
        self, session_id: str, payload: ChatMessageCreate, request: Request
    ) -> dict[str, Any]:
        """
        Send a user message and trigger an ohrs analysis.

        Creates the user message, a placeholder assistant message,
        and publishes a job to the queue.  Returns 202 with the
        assistant message_id that will be filled in by the worker.
        Denies the request with HTTP 429 when the user has already
        hit the daily analysis quota — the ``Retry-After`` and
        ``X-Quota-Reset-At`` headers tell the client when to retry.
        """
        user = self._get_current_user(request)
        trace_id = TraceContext.new()

        session = await self._store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.user_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")

        try:
            quota_snapshot = await self._quota.assert_allowed(user.id)
        except QuotaExceededError as exc:
            snapshot = exc.status
            await self._recorder.record(
                user_id=user.id,
                event_type="quota_limit_hit",
                session_id=session_id,
                properties={
                    "used": snapshot.used,
                    "limit": snapshot.limit,
                },
            )
            retry_after = max(
                1,
                int((snapshot.resets_at - datetime.now(timezone.utc)).total_seconds()),
            )
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "quota_exceeded",
                    "message": (
                        "Je dagelijkse limiet van "
                        f"{snapshot.limit} analyses is bereikt. "
                        "Probeer morgen opnieuw of vraag meer toegang aan "
                        "via feedback."
                    ),
                    "used": snapshot.used,
                    "limit": snapshot.limit,
                    "resets_at": snapshot.resets_at.isoformat(),
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-Quota-Reset-At": snapshot.resets_at.isoformat(),
                    "X-Quota-Used": str(snapshot.used),
                    "X-Quota-Limit": str(snapshot.limit),
                },
            ) from exc

        # Snapshot prior session history BEFORE creating the new turn's
        # user/placeholder messages so ``history`` contains only completed
        # exchanges. The executor uses this to detect follow-ups and scope
        # the prompt without re-running all tools.
        prior_messages = await self._store.list_messages(session_id)
        history: list[dict[str, str]] = []
        for m in prior_messages:
            if m.role not in (MessageRole.USER, MessageRole.ASSISTANT):
                continue
            # Skip any lingering thinking/streaming placeholder from a prior
            # turn; only settled content belongs in the context we send.
            if m.role == MessageRole.ASSISTANT and m.status in (
                MessageStatus.THINKING,
                MessageStatus.STREAMING,
            ):
                continue
            history.append(
                {"role": m.role.value, "content": (m.content or "")[:2000]}
            )

        user_msg = ChatMessage(
            id=generate_id(),
            session_id=session_id,
            role=MessageRole.USER,
            content=payload.content,
            status=MessageStatus.COMPLETE,
        )
        await self._store.create_message(user_msg)

        # Empty placeholder content on purpose: while the message is in-flight
        # (thinking/streaming) the UI renders the waiting animation, not a text
        # bubble. A non-empty placeholder used to leak through as a literal
        # "Analyse wordt gestart..." bubble whenever the client observed the
        # message after it had already flipped thinking->streaming.
        assistant_msg = ChatMessage(
            id=generate_id(),
            session_id=session_id,
            role=MessageRole.ASSISTANT,
            content="",
            status=MessageStatus.THINKING,
        )
        await self._store.create_message(assistant_msg)

        messages = await self._store.list_messages(session_id)
        is_first_message = len(messages) <= 2
        if is_first_message:
            title = payload.content[:60]
            await self._store.update_session_title(session_id, title)

        # Fire-and-forget topic tagging on the very first user message in a
        # session. Must never block the 202 response or raise — any failure
        # (missing API key, network error, quota) is swallowed inside the task.
        if is_first_message and (session.topic is None or session.topic == ""):
            asyncio.create_task(
                self._tag_session_topic(session_id, payload.content)
            )

        # Mode lives on the session row (chosen on the landing-page
        # chooser at session creation). Read it back here so the executor
        # picks the right ModeConfig — system prompt, workspace dir, and
        # skill set are all determined by this single field.
        session_mode = session.mode
        skill_for_mode = (
            "capelle-jeugdzorg" if session_mode == "jeugdzorg" else "capelle-analyse"
        )
        job = JobMessage(
            session_id=session_id,
            message_id=assistant_msg.id,
            user_id=user.id,
            query=payload.content,
            skill=skill_for_mode,
            trace_id=trace_id,
            mode=session_mode,
            history=history,
            # Thread node scope through for node-scoped follow-ups (Task 5).
            # None when the user sent a regular (non-node-scoped) message.
            node_id=payload.node_id,
            node_ids=payload.node_ids,
            # Add-in grounding: the open document's text, if supplied. None for
            # ordinary web-chat messages.
            document_context=payload.document_context,
        )
        await self._publisher.publish("chat_jobs", job)

        await self._recorder.record(
            user_id=user.id,
            event_type="message_sent",
            session_id=session_id,
            properties={
                "message_id": assistant_msg.id,
                "query_length": len(payload.content),
                "is_first_message": is_first_message,
                "quota_used": quota_snapshot.used,
                "quota_remaining": quota_snapshot.remaining,
            },
        )

        _logger.info(
            "message_sent",
            session_id=session_id,
            message_id=assistant_msg.id,
            query=payload.content,
            history_len=len(history),
        )
        return {
            "user_message_id": user_msg.id,
            "assistant_message_id": assistant_msg.id,
            "status": "thinking",
        }

    async def list_messages(self, session_id: str, request: Request) -> dict[str, Any]:
        """List all messages in a session, oldest first."""
        user = self._get_current_user(request)
        session = await self._store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.user_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")
        messages = await self._store.list_messages(session_id)
        return {
            "messages": [m.model_dump() for m in messages],
            "total": len(messages),
        }

    async def search_messages(
        self, request: Request, q: str = Query(..., min_length=0)
    ) -> dict[str, Any]:
        """
        Full-text search messages owned by the authenticated user.

        Validates the query length, delegates to the store (which enforces
        tenant isolation via a JOIN on ``chat_sessions.user_id``), and
        returns up to 50 matching messages newest-first.
        """
        user = self._get_current_user(request)
        query_clean = q.strip()
        if len(query_clean) < 2:
            raise HTTPException(
                status_code=400,
                detail="Zoekopdracht moet minimaal 2 tekens lang zijn.",
            )
        results = await self._store.search_messages(user.id, query_clean, limit=50)
        return {
            "messages": [m.model_dump() for m in results],
            "total": len(results),
        }

    async def _tag_session_topic(self, session_id: str, content: str) -> None:
        """
        Best-effort topic extraction via a cheap OpenAI chat completion.

        Stores a 1-3 word Dutch topic on the session.  Any error —
        missing API key, transport failure, unexpected response — is
        logged and swallowed so the user's conversation is never blocked.
        The chat completion is wrapped in ``asyncio.to_thread`` because
        the OpenAI SDK's sync client is the most portable across
        runtime versions, and we don't want to couple this best-effort
        path to a specific async SDK release.
        """
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            _logger.info("topic_tag_skipped", reason="no_api_key")
            return
        try:
            topic = await asyncio.to_thread(
                self._extract_topic_sync, api_key, content
            )
            if not topic:
                return
            await self._store.update_session_topic(session_id, topic)
            _logger.info(
                "topic_tag_applied", session_id=session_id, topic=topic
            )
        except Exception as exc:  # noqa: BLE001 — best-effort
            _logger.info(
                "topic_tag_failed",
                session_id=session_id,
                error=type(exc).__name__,
                detail=str(exc)[:200],
            )

    # --- Fork links (Wave 3 C2) ---

    async def create_fork_link(
        self, session_id: str, request: Request
    ) -> dict[str, Any]:
        """
        Mint a shareable read-only URL for ``session_id``.

        Caller must own the session. The URL base is the configured
        ``CAPELLE_PUBLIC_ORIGIN`` — a single canonical origin keeps fork
        links stable even when the request came through an internal
        hostname (e.g. tailnet FQDN) rather than the user-facing domain.
        """
        user = self._get_current_user(request)
        session = await self._store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.user_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")
        token, expires_at = await self._store.create_fork_token(session_id)
        base = self._settings.public_origin
        url = f"{base.rstrip('/')}/f/{token}"
        _logger.info(
            "fork_link_created",
            session_id=session_id,
            user_id=user.id,
            expires_at=expires_at,
        )
        await self._recorder.record(
            user_id=user.id,
            event_type="share_link_minted",
            session_id=session_id,
            properties={"token_prefix": token[:8]},
        )
        return {"url": url, "token": token, "expires_at": expires_at}

    async def get_fork_view(self, token: str) -> dict[str, Any]:
        """
        Serve the read-only snapshot behind ``token``.

        Intentionally un-authenticated — the token is the capability. A
        missing or expired token returns a flat 404 so scanners can't
        distinguish "never existed" from "already expired".
        """
        session = await self._store.resolve_fork_token(token)
        if session is None:
            raise HTTPException(status_code=404, detail="Fork link not found or expired")
        messages = await self._store.list_messages(session.id)
        # Attribute the "opened" event to the session owner so the
        # dashboard funnel connects minted → opened under one user_id.
        # The viewer is anonymous by design; we only know "someone opened
        # this link", which is what the owner cares about.
        await self._recorder.record(
            user_id=session.user_id,
            event_type="share_link_opened",
            session_id=session.id,
            properties={"token_prefix": token[:8]},
        )
        return {
            "session": session.model_dump(),
            "messages": [m.model_dump() for m in messages],
        }

    async def adopt_fork(
        self, token: str, request: Request
    ) -> dict[str, Any]:
        """
        Clone the forked session into the authenticated user's account.

        Returns the new session ID so the frontend can navigate the
        recipient straight into their new copy. The original session and
        its owner are untouched.
        """
        user = self._get_current_user(request)
        source = await self._store.resolve_fork_token(token)
        if source is None:
            raise HTTPException(status_code=404, detail="Fork link not found or expired")
        new_session = await self._store.clone_session_for_user(source.id, user.id)
        _logger.info(
            "fork_adopted",
            source_session_id=source.id,
            new_session_id=new_session.id,
            user_id=user.id,
        )
        # Record the adopt under the adopter's user_id so DAU-by-user
        # stats stay accurate. Source owner still sees "opened" on
        # their share funnel from ``get_fork_view``.
        await self._recorder.record(
            user_id=user.id,
            event_type="share_link_adopted",
            session_id=new_session.id,
            properties={
                "source_session_id": source.id,
                "source_owner_id": source.user_id,
                "token_prefix": token[:8],
            },
        )
        return {"session_id": new_session.id, "session": new_session.model_dump()}

    @staticmethod
    def _extract_topic_sync(api_key: str, content: str) -> str:
        """
        Run one blocking chat completion and return a sanitised topic.

        Kept sync + small so the caller can offload it with
        ``asyncio.to_thread``.  Max tokens is tiny; model is the cheapest
        tier available. Output is normalised: strip punctuation and
        trailing whitespace, lowercase to keep grouping consistent.
        """
        from openai import OpenAI  # local import; keeps module import cheap

        client = OpenAI(api_key=api_key)
        trimmed = content[:500]
        prompt = (
            "Extract a 1-3 word Dutch topic tag from this municipal query: "
            f"{trimmed}. Respond with ONLY the topic, no punctuation."
        )
        # Model name per spec — caller may substitute via env later.
        model = os.environ.get("CAPELLE_TOPIC_MODEL", "gpt-4o-mini")
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8,
            temperature=0.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        # strip surrounding quotes/punctuation, collapse whitespace
        cleaned = raw.strip().strip(".,;:!?\"'`").strip().lower()
        # cap to 3 words max as a final safety net
        parts = cleaned.split()
        return " ".join(parts[:3]) if parts else ""
