"""
Analysis handler.

Exposes endpoints for submitting, listing, and retrieving analyses.
Submission publishes a job to the queue and returns 202 Accepted.
Decision logic only — execution is handled by the worker pool.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from capelle_platform.auth.base_authenticator import BaseAuthenticator
from capelle_platform.elicitation.base_registry import BaseElicitationRegistry
from capelle_platform.models.analysis import (
    Analysis,
    AnalysisCreate,
    AnalysisStatus,
    AnalysisSummary,
)
from capelle_platform.models.job import JobMessage
from capelle_platform.models.user import User
from capelle_platform.observability import get_logger
from capelle_platform.observability.logger import TraceContext
from capelle_platform.settings import Settings
from capelle_platform.queue.base_queue import BasePublisher
from capelle_platform.store.base_store import BaseStore
from capelle_platform.utils import generate_id

_logger = get_logger(__name__)

# Upper bound on the answer text accepted from the browser (API-8). Bounded so
# a malicious client cannot push an unbounded body into the elicitation flow.
_MAX_ANSWER_LENGTH: int = 8_000
# Number of leading hex chars of the query SHA-256 logged in lieu of the raw
# text (API-8) — enough to correlate, never enough to reconstruct PII.
_QUERY_HASH_PREFIX_LEN: int = 12


class SubmitAnswerRequest(BaseModel):
    """
    Typed body for ``POST /api/analyses/answer`` (API-8).

    ``answer`` is length-capped so an oversized body yields a 422 rather than
    being silently coerced and broadcast.
    """

    model_config = {"extra": "ignore"}

    question_id: str = Field(min_length=1, max_length=128)
    answer: str = Field(default="", max_length=_MAX_ANSWER_LENGTH)


def _hash_query(query: str) -> str:
    """
    Return a short, irreversible fingerprint of ``query`` for logging (API-8).

    The raw query can be PII (Dutch municipal questions), so logs carry only a
    truncated SHA-256 hex digest plus the length — correlatable, not readable.
    """
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()
    return digest[:_QUERY_HASH_PREFIX_LEN]


class AnalysisHandler:
    """
    HTTP handler for analysis CRUD and job submission.

    Separates the decision to run an analysis (this handler) from
    the actual execution (worker pool + executor).
    """

    def __init__(
        self,
        store: BaseStore,
        publisher: BasePublisher,
        authenticator: BaseAuthenticator,
        settings: Settings,
        registry: BaseElicitationRegistry | None = None,
    ) -> None:
        """
        Construct with injected dependencies.

        Args:
            store: Persistence layer for analysis records.
            publisher: Queue publisher to dispatch jobs.
            authenticator: Token validator for extracting the current user.
            registry: Elicitation registry for resolving mid-run agent
                questions. When ``None``, the ``/api/analyses/answer`` route
                returns 501.
        """
        self._store = store
        self._publisher = publisher
        self._auth = authenticator
        self._settings = settings
        self._registry = registry
        self.router = APIRouter(tags=["analyses"])
        self.router.add_api_route(
            "/api/analyses", self.submit_analysis, methods=["POST"], status_code=202
        )
        self.router.add_api_route(
            "/api/analyses", self.list_analyses, methods=["GET"]
        )
        self.router.add_api_route(
            "/api/analyses/{analysis_id}", self.get_analysis, methods=["GET"]
        )
        self.router.add_api_route(
            "/api/analyses/answer", self.submit_answer, methods=["POST"]
        )

    def _get_current_user(self, request: Request) -> User:
        """
        Extract and validate the user from the auth cookie or Bearer header.

        The HttpOnly cookie is the web frontend's credential (the token is
        intentionally not exposed to JS), so it must be accepted here — the
        browser POSTs the elicitation answer with the cookie, not a Bearer
        header. A Bearer header is still honoured for programmatic clients.
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

    async def submit_analysis(
        self, payload: AnalysisCreate, request: Request
    ) -> dict[str, str]:
        """
        Accept an analysis request, persist it, and publish a job.

        Returns 202 with the analysis_id.  The actual work happens
        asynchronously in the worker pool.
        """
        user = self._get_current_user(request)
        trace_id = TraceContext.new()
        analysis_id = generate_id()

        analysis = Analysis(
            id=analysis_id,
            user_id=user.id,
            query=payload.query,
            skill=payload.skill,
            status=AnalysisStatus.QUEUED,
        )
        await self._store.create_analysis(analysis)

        job = JobMessage(
            analysis_id=analysis_id,
            user_id=user.id,
            query=payload.query,
            skill=payload.skill,
            trace_id=trace_id,
        )
        await self._publisher.publish("analyses", job)

        _logger.info(
            "analysis_submitted",
            analysis_id=analysis_id,
            user_id=user.id,
            trace_id=trace_id,
            query_hash=_hash_query(payload.query),
            query_length=len(payload.query),
        )
        return {"id": analysis_id, "status": "queued"}

    async def list_analyses(
        self, request: Request
    ) -> dict[str, Any]:
        """
        List all analyses for the authenticated user, newest first.

        Returns lightweight summaries without the full result payload.
        """
        user = self._get_current_user(request)
        analyses = await self._store.list_analyses(user.id)
        return {
            "analyses": [a.model_dump() for a in analyses],
            "total": len(analyses),
        }

    async def get_analysis(
        self, analysis_id: str, request: Request
    ) -> dict[str, Any]:
        """
        Fetch a single analysis by ID with the full result payload.

        Returns 404 if not found, 403 if it belongs to another user.
        """
        user = self._get_current_user(request)
        analysis = await self._store.get_analysis(analysis_id)
        if analysis is None:
            raise HTTPException(status_code=404, detail="Analysis not found")
        if analysis.user_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")
        return analysis.model_dump()

    async def submit_answer(self, request: Request) -> dict[str, Any]:
        """
        Deliver a user's answer to a pending mid-run elicitation question.

        Authenticated via the standard Bearer token. The body must carry:

        .. code-block:: json

            {"question_id": "<hex>", "answer": "<user text or option>"}

        Returns ``{"resolved": true}`` when the answer was accepted;
        ``{"resolved": false}`` if the question is unknown, already done, or
        owned by a different user (API-1 — the ownership check lives in the
        registry, which rejects a mismatch without revealing existence).
        Returns 501 if the elicitation registry is not configured.
        """
        user = self._get_current_user(request)  # raises 401 if unauthenticated

        if self._registry is None:
            raise HTTPException(
                status_code=501,
                detail="Elicitation registry not configured",
            )

        try:
            body: dict[str, Any] = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

        # Typed + bounded parse (API-8): 422 on overflow or a missing id.
        try:
            parsed = SubmitAnswerRequest.model_validate(body)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="Invalid request body"
            ) from exc

        # The registry enforces that the caller owns the question (API-1).
        resolved: bool = self._registry.resolve(
            parsed.question_id, parsed.answer, user.id
        )
        _logger.info(
            "elicitation_answer_submitted",
            question_id=parsed.question_id,
            user_id=user.id,
            resolved=resolved,
        )
        return {"resolved": resolved}
