"""
Internal graph handler — localhost-only write endpoints for the finding-graph.

Exposes:
  POST /internal/graph/{message_id}/nodes    → 201 | 409 | 422 | 404
  POST /internal/graph/{message_id}/edges    → 201 | 404 | 422
  POST /internal/graph/{message_id}/summary  → 204 | 404
  GET  /internal/graph/{message_id}          → 200 (full or compact)

Security model (mirrors ``InternalHandler`` for ``/internal/ask``):

1. Reject any caller whose host is not loopback (127.0.0.1 / ::1).
2. Additionally gated by ``settings.graph_enabled`` — when False all four
   routes immediately return 404 so the platform behaves exactly as it did
   before the finding-graph feature.

Session-id resolution: ``message_id`` is mapped to ``session_id`` via
``store.get_message`` → ``store.get_session``, identical to
``InternalHandler._resolve_user_id``. A ``message_id`` that does not resolve
gets a 404 response (the spec's "RUNNING-job gate" — only messages that exist
in the store are valid, and every message in the store belongs to a session).

The handler does NOT mint node/edge ids — it reads them from the request body
or assigns them via ``generate_id()`` before handing to the service.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from capelle_platform.graph.base_store import LimitExceededError
from capelle_platform.graph.models import GraphEdge, GraphNode
from capelle_platform.graph.service import (
    DuplicateNodeError,
    GraphService,
    UnknownNodeError,
)
from capelle_platform.observability import get_logger
from capelle_platform.observability.logger import TraceContext
from capelle_platform.settings import Settings
from capelle_platform.store.base_store import BaseStore
from capelle_platform.utils import generate_id
from capelle_platform.utils.hook_auth import (
    HOOK_SIGNATURE_HEADER,
    verify_hook_signature,
)

_logger = get_logger(__name__)

# Resolved loopback IPs only — mirrors the constant in internal.py.
_LOCALHOST_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1"})


# ---------------------------------------------------------------------------
# Request body schemas
# ---------------------------------------------------------------------------


class _NodeCreateRequest(BaseModel):
    """
    Request body for ``POST /internal/graph/{message_id}/nodes``.

    The id is server-assigned; the handler mints it via ``generate_id()``.
    All remaining fields are forwarded to ``GraphNode``.
    """

    model_config = {"extra": "ignore"}

    node_type: str
    claim: str
    confidence: str
    status: str = "proposed"
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    agent_label: str = ""
    trace_id: str | None = None


class _EdgeCreateRequest(BaseModel):
    """Request body for ``POST /internal/graph/{message_id}/edges``."""

    model_config = {"extra": "ignore"}

    source_id: str
    target_id: str
    edge_type: str
    rationale: str = ""


class _SummaryRequest(BaseModel):
    """Request body for ``POST /internal/graph/{message_id}/summary``."""

    model_config = {"extra": "ignore"}

    text: str


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


class GraphInternalHandler:
    """
    HTTP handler for localhost-only finding-graph write endpoints.

    Follows the same class-based pattern as ``InternalHandler``: constructor
    accepts injected dependencies; routes are registered in ``__init__``; each
    method is an async handler that guards, resolves, and delegates.
    """

    def __init__(
        self,
        service: GraphService,
        store: BaseStore,
        settings: Settings | None = None,
    ) -> None:
        """
        Construct with injected dependencies.

        Args:
            service: ``GraphService`` instance (carries store + broadcast).
            store: Persistence layer used to resolve message_id → session_id.
            settings: Application settings. If None, ``Settings()`` is used.
        """
        self._service = service
        self._store = store
        self._settings = settings or Settings()
        self.router = APIRouter(tags=["internal-graph"])
        self.router.add_api_route(
            "/internal/graph/{message_id}/nodes",
            self.add_node,
            methods=["POST"],
            status_code=201,
        )
        self.router.add_api_route(
            "/internal/graph/{message_id}/edges",
            self.add_edge,
            methods=["POST"],
            status_code=201,
        )
        self.router.add_api_route(
            "/internal/graph/{message_id}/summary",
            self.set_summary,
            methods=["POST"],
            status_code=204,
        )
        self.router.add_api_route(
            "/internal/graph/{message_id}",
            self.get_graph,
            methods=["GET"],
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_localhost(self, request: Request) -> JSONResponse | None:
        """Return a 403 JSONResponse if the caller is not loopback, else None."""
        client_host = request.client.host if request.client else ""
        if client_host not in _LOCALHOST_HOSTS:
            _logger.warning(
                "graph_internal_rejected_non_local",
                client_host=client_host,
            )
            return JSONResponse(
                status_code=403,
                content={"error": "forbidden"},
            )
        return None

    def _check_signature(
        self, request: Request, message_id: str
    ) -> JSONResponse | None:
        """
        HMAC defence-in-depth, mirroring ``/internal/ask`` (API-5): when
        ``settings.internal_hook_secret`` is configured the caller must sign
        the message_id; no-op when no secret is set. Keeps the graph routes
        at security parity with the other internal endpoints.
        """
        signature = request.headers.get(HOOK_SIGNATURE_HEADER)
        if not verify_hook_signature(
            self._settings.internal_hook_secret, message_id, signature
        ):
            _logger.warning(
                "graph_internal_rejected_bad_signature",
                message_id=message_id,
            )
            return JSONResponse(
                status_code=403,
                content={"error": "forbidden"},
            )
        return None

    def _check_flag(self) -> JSONResponse | None:
        """Return a 404 JSONResponse if graph_enabled is False, else None."""
        if not self._settings.graph_enabled:
            return JSONResponse(
                status_code=404,
                content={"error": "graph feature disabled"},
            )
        return None

    async def _resolve_session_id(self, message_id: str) -> str | None:
        """
        Map ``message_id`` → ``session_id`` via the store.

        Returns ``None`` when either the message or its session is absent.
        Mirrors ``InternalHandler._resolve_user_id`` without the extra
        session→user hop (we need the session_id, not the user_id here).
        """
        message = await self._store.get_message(message_id)
        if message is None:
            return None
        return message.session_id

    async def _resolve_user_id(self, message_id: str) -> str | None:
        """
        Map ``message_id`` → ``user_id`` for broadcast targeting.

        Walks message → session → user. Returns None if any link is missing.
        """
        message = await self._store.get_message(message_id)
        if message is None:
            return None
        session = await self._store.get_session(message.session_id)
        if session is None:
            return None
        return session.user_id

    # ------------------------------------------------------------------
    # Route handlers
    # ------------------------------------------------------------------

    async def add_node(
        self, message_id: str, request: Request
    ) -> Response:
        """
        Accept a new graph node from the running agent.

        Guards: loopback, flag, message existence.
        Delegates dedup and persistence to ``GraphService.add_node``.

        Returns 201 + node JSON on success, or:
          - 403 if caller is not localhost.
          - 404 if ``settings.graph_enabled`` is False or message is unknown.
          - 409 ``{"detail": {"duplicate_of": "<id>"}}`` on dedup collision.
          - 422 if the node body is structurally invalid.
        """
        trace_id = TraceContext.new()

        guard = (
            self._check_localhost(request)
            or self._check_flag()
            or self._check_signature(request, message_id)
        )
        if guard is not None:
            return guard

        session_id = await self._resolve_session_id(message_id)
        if session_id is None:
            _logger.info(
                "graph_internal_unknown_message",
                message_id=message_id,
                trace_id=trace_id,
            )
            return JSONResponse(
                status_code=404,
                content={"error": "unknown message"},
            )

        try:
            body: dict[str, Any] = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(
                status_code=422,
                content={"error": "invalid json body"},
            )

        try:
            parsed = _NodeCreateRequest.model_validate(body)
        except ValidationError as exc:
            return JSONResponse(
                status_code=422,
                content={"detail": exc.errors()},
            )

        node_id = generate_id()
        try:
            node = GraphNode(
                id=node_id,
                node_type=parsed.node_type,  # type: ignore[arg-type]
                claim=parsed.claim,
                confidence=parsed.confidence,  # type: ignore[arg-type]
                status=parsed.status,  # type: ignore[arg-type]
                blocks=parsed.blocks,
                citations=parsed.citations,
                agent_label=parsed.agent_label,
                message_id=message_id,
                trace_id=trace_id,
            )
        except (ValueError, ValidationError) as exc:
            return JSONResponse(
                status_code=422,
                content={"detail": str(exc)},
            )

        try:
            stored = await self._service.add_node(session_id, message_id, node)
        except DuplicateNodeError as exc:
            _logger.info(
                "graph_internal_dedup",
                message_id=message_id,
                existing_id=exc.existing_id,
                trace_id=trace_id,
            )
            return JSONResponse(
                status_code=409,
                content={"detail": {"duplicate_of": exc.existing_id}},
            )
        except LimitExceededError as exc:
            return JSONResponse(
                status_code=422,
                content={"detail": str(exc)},
            )

        return JSONResponse(
            status_code=201,
            content=stored.model_dump(mode="json"),
        )

    async def add_edge(
        self, message_id: str, request: Request
    ) -> Response:
        """
        Accept a new graph edge from the running agent.

        Both endpoint node ids must already exist in the session graph.
        Returns 201 + edge JSON, or 404 on unknown message / endpoint node.
        """
        trace_id = TraceContext.new()

        guard = (
            self._check_localhost(request)
            or self._check_flag()
            or self._check_signature(request, message_id)
        )
        if guard is not None:
            return guard

        session_id = await self._resolve_session_id(message_id)
        if session_id is None:
            return JSONResponse(
                status_code=404,
                content={"error": "unknown message"},
            )

        try:
            body: dict[str, Any] = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(
                status_code=422,
                content={"error": "invalid json body"},
            )

        try:
            parsed = _EdgeCreateRequest.model_validate(body)
        except ValidationError as exc:
            return JSONResponse(
                status_code=422,
                content={"detail": exc.errors()},
            )

        edge_id = generate_id()
        try:
            edge = GraphEdge(
                id=edge_id,
                source_id=parsed.source_id,
                target_id=parsed.target_id,
                edge_type=parsed.edge_type,  # type: ignore[arg-type]
                rationale=parsed.rationale,
            )
        except (ValueError, ValidationError) as exc:
            return JSONResponse(
                status_code=422,
                content={"detail": str(exc)},
            )

        try:
            stored = await self._service.add_edge(session_id, message_id, edge)
        except UnknownNodeError as exc:
            _logger.info(
                "graph_internal_unknown_node",
                message_id=message_id,
                node_id=exc.node_id,
                trace_id=trace_id,
            )
            return JSONResponse(
                status_code=404,
                content={"error": f"unknown node: {exc.node_id}"},
            )
        except LimitExceededError as exc:
            return JSONResponse(
                status_code=422,
                content={"detail": str(exc)},
            )

        return JSONResponse(
            status_code=201,
            content=stored.model_dump(mode="json"),
        )

    async def set_summary(
        self, message_id: str, request: Request
    ) -> Response:
        """
        Replace the session graph's summary text.

        Returns 204 No Content on success.
        """
        trace_id = TraceContext.new()

        guard = (
            self._check_localhost(request)
            or self._check_flag()
            or self._check_signature(request, message_id)
        )
        if guard is not None:
            return guard

        session_id = await self._resolve_session_id(message_id)
        if session_id is None:
            return JSONResponse(
                status_code=404,
                content={"error": "unknown message"},
            )

        try:
            body: dict[str, Any] = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(
                status_code=422,
                content={"error": "invalid json body"},
            )

        try:
            parsed = _SummaryRequest.model_validate(body)
        except ValidationError as exc:
            return JSONResponse(
                status_code=422,
                content={"detail": exc.errors()},
            )

        await self._service.set_summary(session_id, message_id, parsed.text)

        _logger.info(
            "graph_internal_summary_set",
            message_id=message_id,
            session_id=session_id,
            trace_id=trace_id,
        )
        return Response(status_code=204)

    async def get_graph(
        self,
        message_id: str,
        request: Request,
        compact: bool = Query(default=False, alias="compact"),
    ) -> Response:
        """
        Return the current finding-graph for the session.

        ``?compact=1`` returns the compact shape (id, claim, node_type per
        node; edges as [source, target, type] triples) for agent-side dedup.
        Without the parameter, returns the full ``FindingGraph`` JSON.

        Returns 200 with the graph body. When the session has no graph yet,
        returns 200 with an empty graph ``{"nodes": [], "edges": [], ...}``.
        """
        trace_id = TraceContext.new()

        guard = (
            self._check_localhost(request)
            or self._check_flag()
            or self._check_signature(request, message_id)
        )
        if guard is not None:
            return guard

        session_id = await self._resolve_session_id(message_id)
        if session_id is None:
            return JSONResponse(
                status_code=404,
                content={"error": "unknown message"},
            )

        if compact:
            result = await self._service.get_compact(session_id)
            if result is None:
                result = {"nodes": [], "edges": []}
            return JSONResponse(status_code=200, content=result)

        graph = await self._service.get(session_id)
        if graph is None:
            # Return an empty graph representation (session exists but no
            # graph mutations have been made yet).
            return JSONResponse(
                status_code=200,
                content={
                    "session_id": session_id,
                    "nodes": [],
                    "edges": [],
                    "summary": None,
                    "version": 0,
                },
            )

        return JSONResponse(
            status_code=200,
            content=graph.model_dump(mode="json"),
        )
