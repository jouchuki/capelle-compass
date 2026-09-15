"""
Internal handler — localhost-only endpoints for ohrs hook callbacks.

ohrs is configured with an HTTP ``post_tool_use`` hook that POSTs each
tool-use event to ``/internal/hook/{message_id}``. The handler translates
the payload into a human-readable Dutch action string and broadcasts it
via the WebSocketHub so the frontend timeline updates in real time —
long before ohrs exits and the trajectory.jsonl file is flushed.

Additionally exposes ``POST /internal/ask/{message_id}`` — a blocking
endpoint that lets the ohrs subprocess (via the ``capelle-ask`` CLI)
pause execution to ask the user a question over the WebSocket and wait
for the authenticated answer before returning.

Security (defence-in-depth):

1. Both endpoints reject any client whose host is not loopback
   (``127.0.0.1``/``::1``) — ohrs runs as a subprocess of the platform and
   can only reach the API via loopback.
2. Additionally, when ``settings.internal_hook_secret`` is configured, both
   endpoints verify an HMAC-SHA256 signature header (API-5). ``/internal/hook``
   verifies a static signature over the ``message_id``; ``/internal/ask``
   verifies a timestamped signature over the body. This means authenticity no
   longer rests solely on network topology. Never expose these routes on a
   public interface.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from capelle_platform.elicitation.base_registry import BaseElicitationRegistry
from capelle_platform.executor.impl_ohrs import _cmd_invokes, _debrand_cmd
from capelle_platform.observability import get_logger
from capelle_platform.settings import Settings
from capelle_platform.store.base_store import BaseStore
from capelle_platform.utils.hook_auth import (
    HOOK_SIGNATURE_HEADER,
    HOOK_TIMESTAMP_HEADER,
    verify_hook_signature,
    verify_timestamped_signature,
)
from capelle_platform.ws.hub import WebSocketHub

_logger = get_logger(__name__)

# Resolved loopback IPs only — ``request.client.host`` is always a resolved
# address, never the literal string "localhost" (API-5: dropped the dead entry).
_LOCALHOST_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1"})

# WebSocket event type for elicitation questions sent to the browser.
_WS_EVENT_TYPE_ELICITATION: str = "elicitation"

# Upper bound on the elicitation question/answer text the platform accepts
# from the agent (API-8). Generous for a question prompt, but bounded so a
# runaway agent can't post an unbounded body into the WS broadcast.
_MAX_QUESTION_LENGTH: int = 4_000
_MAX_OPTION_LENGTH: int = 1_000
_MAX_OPTIONS: int = 32


class AskRequest(BaseModel):
    """
    Typed body for ``POST /internal/ask`` (API-8).

    Bounds every field so a malformed or runaway agent cannot push an
    unbounded payload; FastAPI returns 422 automatically on overflow.
    """

    model_config = {"extra": "ignore"}

    question: str = Field(default="", max_length=_MAX_QUESTION_LENGTH)
    options: list[str] = Field(default_factory=list, max_length=_MAX_OPTIONS)
    allow_free_text: bool = True


class InternalHandler:
    """
    HTTP handler for ohrs hook callbacks.

    Exposes ``POST /internal/hook/{message_id}`` which accepts a post_tool_use
    event from ohrs and rebroadcasts it on the user's WebSocket channel as a
    ``progress`` event. Responds 204 immediately; any exception is swallowed
    so a slow or failing backend never blocks the ohrs agent.
    """

    def __init__(
        self,
        ws_hub: WebSocketHub,
        store: BaseStore,
        hook_seen_for: Callable[[str], set[str]] | None = None,
        make_dedup_key: Callable[[str, Any], str] | None = None,
        registry: BaseElicitationRegistry | None = None,
        settings: Settings | None = None,
    ) -> None:
        """
        Construct with the WebSocket hub, store, and optional dedup hooks.

        Args:
            ws_hub: Hub used to broadcast the progress event to the user.
            store: Persistence layer — the handler resolves
                ``message_id -> user_id`` through message -> session -> user.
            hook_seen_for: Optional callable returning the per-message dedup
                set shared with the trajectory tailer. When provided, the
                handler records each key it emits so the tailer won't
                broadcast a duplicate later.
            make_dedup_key: Optional callable that builds the dedup key
                from ``(tool_name, tool_input)``. Must be supplied together
                with ``hook_seen_for`` for dedup to take effect.
            registry: Elicitation registry for tracking mid-run questions.
                When ``None``, the ``/internal/ask`` route returns 501.
            settings: Application settings, used for
                ``elicitation_timeout_seconds``. Defaults to ``Settings()``
                when not supplied.
        """
        self._ws_hub = ws_hub
        self._store = store
        self._hook_seen_for = hook_seen_for
        self._make_dedup_key = make_dedup_key
        self._registry = registry
        self._settings = settings or Settings()
        # Strong refs to in-flight background delivery tasks so the event loop
        # does not GC them mid-run (EXEC-6); each removes itself on completion.
        self._background_tasks: set[asyncio.Task[None]] = set()
        self.router = APIRouter(tags=["internal"])
        self.router.add_api_route(
            "/internal/hook/{message_id}",
            self.receive_hook,
            methods=["POST"],
        )
        self.router.add_api_route(
            "/internal/ask/{message_id}",
            self.ask,
            methods=["POST"],
        )

    async def receive_hook(
        self, message_id: str, request: Request
    ) -> Response:
        """
        Accept an ohrs post_tool_use hook payload and broadcast a progress event.

        Returns 204 No Content **immediately** after the cheap auth + parse
        steps; the user-resolution + broadcast work is dispatched to a
        background task (EXEC-6). The ohrs HTTP hook has a 2s timeout, so doing
        the store lookup + WS fan-out inline risked timing it out and dropping
        live progress; deferring it keeps the hook practically non-blocking.
        Any internal error is logged and swallowed so ohrs never blocks on a
        platform issue.
        """
        client_host = request.client.host if request.client else ""
        if client_host not in _LOCALHOST_HOSTS:
            _logger.warning(
                "internal_hook_rejected_non_local",
                client_host=client_host,
                message_id=message_id,
            )
            return Response(status_code=403)

        # HMAC defence-in-depth (API-5). ohrs HTTP hooks can only set *static*
        # headers, so the signature is over the message_id (carried in the URL,
        # fixed for this hook's lifetime). No-op when no secret is configured.
        signature = request.headers.get(HOOK_SIGNATURE_HEADER)
        if not verify_hook_signature(
            self._settings.internal_hook_secret, message_id, signature
        ):
            _logger.warning(
                "internal_hook_rejected_bad_signature",
                message_id=message_id,
            )
            return Response(status_code=403)

        try:
            body: dict[str, Any] = await request.json()
        except Exception:  # noqa: BLE001 — malformed body must not block ohrs
            _logger.info(
                "internal_hook_bad_json",
                message_id=message_id,
            )
            return Response(status_code=204)

        # ohrs wraps hook payloads as { "event": "<name>", "payload": {...} }.
        # Fall back to top-level fields so a future shape change doesn't drop
        # live progress entirely.
        event_name = str(body.get("event") or body.get("payload", {}).get("event") or "").lower()
        inner = body.get("payload") if isinstance(body.get("payload"), dict) else body
        if not isinstance(inner, dict):
            inner = {}

        tool_name = str(inner.get("tool_name") or "").strip()
        tool_input = inner.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            tool_input = {}

        # pre_tool_use → "running"; post_tool_use (and everything else) → "done"
        status = "running" if event_name == "pre_tool_use" else "done"

        # Defer the store lookup + broadcast so the hook returns within its 2s
        # budget regardless of store/WS latency (EXEC-6). The task is fire and
        # forget; a stored reference prevents premature GC.
        task = asyncio.create_task(
            self._deliver_progress(message_id, event_name, status, tool_name, tool_input),
            name=f"hook-deliver-{message_id}",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        return Response(status_code=204)

    async def _deliver_progress(
        self,
        message_id: str,
        event_name: str,
        status: str,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> None:
        """
        Resolve the owning user and broadcast a progress event.

        Runs as a background task spawned by :meth:`receive_hook` so the hook
        itself returns 204 immediately. All failures are logged and swallowed —
        a dropped progress event must never surface as an error.
        """
        try:
            user_id = await self._resolve_user_id(message_id)
        except Exception:  # noqa: BLE001 — lookup must never escape the task
            _logger.exception(
                "internal_hook_user_lookup_failed",
                message_id=message_id,
            )
            return

        if user_id is None:
            _logger.info(
                "internal_hook_unknown_message",
                message_id=message_id,
                tool_name=tool_name,
            )
            return

        action = self._describe_tool_use(tool_name, tool_input)

        # Record the dedup key so the trajectory tailer (fallback path) does
        # not re-broadcast this same tool_use block after ohrs terminates.
        if self._hook_seen_for is not None and self._make_dedup_key is not None:
            try:
                seen = self._hook_seen_for(message_id)
                seen.add(self._make_dedup_key(tool_name, tool_input))
            except Exception:  # noqa: BLE001 — dedup must never block
                _logger.exception(
                    "internal_hook_dedup_record_failed",
                    message_id=message_id,
                )

        event = {
            "type": "progress",
            "message_id": message_id,
            "tool": tool_name,
            "action": action,
            "status": status,
            "source": "hook",
            "event_name": event_name,
        }

        try:
            await self._ws_hub.broadcast_to_user(user_id, event)
            _logger.info(
                "internal_hook_received",
                message_id=message_id,
                user_id=user_id,
                tool_name=tool_name,
                action=action,
            )
        except Exception:  # noqa: BLE001 — broadcast must not escape the task
            _logger.exception(
                "internal_hook_broadcast_failed",
                message_id=message_id,
            )

    async def _resolve_user_id(self, message_id: str) -> str | None:
        """
        Resolve the ``user_id`` that owns the given assistant ``message_id``.

        Walks message -> session -> user. Returns None if any link is missing.
        """
        message = await self._store.get_message(message_id)
        if message is None:
            return None
        session = await self._store.get_session(message.session_id)
        if session is None:
            return None
        return session.user_id

    @staticmethod
    def _describe_tool_use(tool_name: str, tool_input: dict[str, Any]) -> str:
        """
        Generate a human-readable Dutch description of a tool invocation.

        Mirrors :meth:`OhrsExecutor._describe_tool_use` so hook-driven and
        trajectory-driven events render identically in the UI timeline.
        """
        if tool_name == "Bash":
            cmd = str(tool_input.get("command", "") or "")
            # Brand-free labels matching legacy capelle-* names AND the neutral
            # display aliases; shares the helpers with OhrsExecutor so
            # hook- and trajectory-driven events render identically.
            if "capelle-beleid" in cmd or _cmd_invokes(cmd, "beleid"):
                return "Beleid: zoekt documenten..."
            if "capelle-cube" in cmd or _cmd_invokes(cmd, "cube"):
                return "Cube: analyseert tabel..."
            if "cbs " in cmd:
                return f"CBS: {cmd[:80]}"
            if "capelle-budget" in cmd:
                return "Budget: analyseert uitgaven..."
            if "capelle-buitenbeter" in cmd:
                return "BuitenBeter: haalt meldingen op..."
            if "pdftotext" in cmd:
                return "Enquete: leest bewonersenquete..."
            return f"Uitvoeren: {_debrand_cmd(cmd)[:60]}"
        if tool_name == "Skill":
            skill = str(tool_input.get("skill", "") or "")
            return f"Skill: /{skill} gestart"
        if tool_name == "Read":
            path = str(tool_input.get("file_path", "") or "")
            return f"Leest: {Path(path).name}" if path else "Leest bestand..."
        if tool_name == "Grep":
            pattern = str(tool_input.get("pattern", "") or "")
            return f"Zoekt: '{pattern}'"
        if tool_name == "Glob":
            pattern = str(tool_input.get("pattern", "") or "")
            return f"Glob: '{pattern}'"
        return f"{tool_name}: voltooid" if tool_name else "Tool voltooid"

    async def ask(self, message_id: str, request: Request) -> JSONResponse:
        """
        Block an ohrs agent subprocess until the user answers a question.

        The ``capelle-ask`` CLI POSTs here from within the running agent.
        This method:

        1. Guards against non-localhost callers (returns 403 JSON).
        2. Resolves ``message_id`` to a ``user_id``; returns 404 if unknown.
        3. Registers a ``Future`` in the elicitation registry.
        4. Broadcasts an ``elicitation`` WebSocket event to the user's browser.
        5. Awaits the ``Future`` with a configurable timeout.
        6. Returns the answer (or a timed-out indicator) as JSON.

        Request body (JSON):

        .. code-block:: json

            {
                "question": "string",
                "options": ["opt-a", "opt-b"],
                "allow_free_text": true
            }

        Response body (JSON):

        .. code-block:: json

            {"answer": "string", "question_id": "hexstring"}

        On timeout:

        .. code-block:: json

            {"answer": "", "timed_out": true, "question_id": "hexstring"}

        On unknown ``message_id``:

        .. code-block:: json

            {"answer": "", "error": "unknown message"}
        """
        client_host = request.client.host if request.client else ""
        if client_host not in _LOCALHOST_HOSTS:
            _logger.warning(
                "internal_ask_rejected_non_local",
                client_host=client_host,
                message_id=message_id,
            )
            return JSONResponse(
                status_code=403,
                content={"answer": "", "error": "forbidden"},
            )

        if self._registry is None:
            _logger.warning(
                "internal_ask_no_registry",
                message_id=message_id,
            )
            return JSONResponse(
                status_code=501,
                content={"answer": "", "error": "elicitation registry not configured"},
            )

        # Read the raw body once so the HMAC is verified over the exact bytes
        # the CLI signed (API-5). ``request.body()`` is cached by Starlette so
        # ``request.json()`` below re-uses it.
        try:
            raw_body: bytes = await request.body()
        except Exception:  # noqa: BLE001
            raw_body = b""

        secret = self._settings.internal_hook_secret
        if secret:
            timestamp = request.headers.get(HOOK_TIMESTAMP_HEADER)
            signature = request.headers.get(HOOK_SIGNATURE_HEADER)
            if not verify_timestamped_signature(
                secret, timestamp, raw_body, signature
            ):
                _logger.warning(
                    "internal_ask_rejected_bad_signature",
                    message_id=message_id,
                )
                return JSONResponse(
                    status_code=403,
                    content={"answer": "", "error": "forbidden"},
                )

        try:
            body: dict[str, Any] = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(
                status_code=400,
                content={"answer": "", "error": "invalid json body"},
            )

        # Typed + bounded parse (API-8): 422-equivalent on overflow without
        # leaking the request internals to the agent.
        try:
            parsed = AskRequest.model_validate(body)
        except ValueError as exc:
            _logger.info(
                "internal_ask_invalid_body",
                message_id=message_id,
                error=str(exc)[:200],
            )
            return JSONResponse(
                status_code=422,
                content={"answer": "", "error": "invalid request body"},
            )

        question: str = parsed.question
        options: list[str] = [o[:_MAX_OPTION_LENGTH] for o in parsed.options]
        allow_free_text: bool = parsed.allow_free_text

        user_id = await self._resolve_user_id(message_id)
        if user_id is None:
            _logger.info(
                "internal_ask_unknown_message",
                message_id=message_id,
            )
            return JSONResponse(
                status_code=404,
                content={"answer": "", "error": "unknown message"},
            )

        # Resolve per-mode elicitation window. Load the session to read its
        # mode field; fall back to the global setting if the mode is absent or
        # unresolvable (e.g. a legacy session that pre-dates the mode column).
        elicit_timeout: float = float(self._settings.elicitation_timeout_seconds)
        try:
            msg = await self._store.get_message(message_id)
            if msg is not None:
                session = await self._store.get_session(msg.session_id)
                mode = getattr(session, "mode", None) if session is not None else None
                if mode is not None:
                    from capelle_platform.executor.modes.factory import ModeConfigFactory
                    elicit_timeout = float(
                        ModeConfigFactory.from_mode(
                            mode, self._settings
                        ).elicitation_timeout_seconds
                    )
        except Exception:  # noqa: BLE001 — mode resolution must never block the ask
            _logger.warning(
                "internal_ask_mode_resolution_failed",
                message_id=message_id,
            )

        question_id: str = uuid4().hex
        future = await self._registry.register(question_id, user_id)

        event: dict[str, Any] = {
            "type": _WS_EVENT_TYPE_ELICITATION,
            "message_id": message_id,
            "question_id": question_id,
            "question": question,
            "options": options,
            "allow_free_text": allow_free_text,
        }

        try:
            await self._ws_hub.broadcast_to_user(user_id, event)
            _logger.info(
                "internal_ask_broadcast",
                message_id=message_id,
                user_id=user_id,
                question_id=question_id,
            )
        except Exception:  # noqa: BLE001
            _logger.exception(
                "internal_ask_broadcast_failed",
                message_id=message_id,
                question_id=question_id,
            )
            # Continue waiting — the user may still answer via polling/other path.

        try:
            answer: str = await asyncio.wait_for(
                future,
                timeout=elicit_timeout,
            )
            _logger.info(
                "internal_ask_answered",
                message_id=message_id,
                question_id=question_id,
            )
            return JSONResponse(
                status_code=200,
                content={"answer": answer, "question_id": question_id},
            )
        except asyncio.TimeoutError:
            _logger.info(
                "internal_ask_timed_out",
                message_id=message_id,
                question_id=question_id,
                timeout=elicit_timeout,
            )
            return JSONResponse(
                status_code=200,
                content={"answer": "", "timed_out": True, "question_id": question_id},
            )
        finally:
            self._registry.discard(question_id)
