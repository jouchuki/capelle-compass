"""
Authenticated read handler for the finding-graph.

Exposes:
  GET /api/chat/sessions/{session_id}/graph → 200 FindingGraph | 404 | 401 | 403

The route pattern mirrors ``ChatHandler.get_session``: authenticate via the
same ``_get_current_user`` mechanism (HttpOnly cookie or Bearer header), then
verify the session belongs to the requesting user. Returns the full
``FindingGraph`` when it exists, otherwise 404.

Also returns 404 when ``settings.graph_enabled`` is False so the feature is
fully transparent from the client's perspective — the API surface simply
doesn't exist.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from capelle_platform.auth.base_authenticator import BaseAuthenticator
from capelle_platform.graph.service import GraphService
from capelle_platform.models.user import User
from capelle_platform.observability import get_logger
from capelle_platform.settings import Settings
from capelle_platform.store.base_store import BaseStore

_logger = get_logger(__name__)


class GraphReadHandler:
    """
    HTTP handler for the authenticated finding-graph read endpoint.

    Registered by the ``AppBuilder`` whenever ``graph_enabled`` is True
    (the builder can choose to skip registration entirely when the flag is
    off; alternatively the handler returns 404 inline — we do the latter so
    the route is always discoverable for ops introspection even when the
    feature is disabled).
    """

    def __init__(
        self,
        service: GraphService,
        store: BaseStore,
        authenticator: BaseAuthenticator,
        settings: Settings | None = None,
    ) -> None:
        """
        Construct with injected dependencies.

        Args:
            service: ``GraphService`` for ``get(session_id)``.
            store: ``BaseStore`` used to resolve session ownership.
            authenticator: Token validator (mirrors ``ChatHandler``).
            settings: Application settings.
        """
        self._service = service
        self._store = store
        self._auth = authenticator
        self._settings = settings or Settings()
        self.router = APIRouter(tags=["graph"])
        self.router.add_api_route(
            "/api/chat/sessions/{session_id}/graph",
            self.get_graph,
            methods=["GET"],
        )

    def _get_current_user(self, request: Request) -> User:
        """
        Extract and validate the current user.

        Mirrors ``ChatHandler._get_current_user`` exactly: HttpOnly cookie
        first, then ``Authorization: Bearer`` header. Raises 401 when neither
        is present or the token is invalid.
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

    async def get_graph(
        self, session_id: str, request: Request
    ) -> dict[str, Any]:
        """
        Return the full ``FindingGraph`` for a session.

        Steps:
        1. Return 404 immediately if ``graph_enabled`` is False.
        2. Authenticate the caller.
        3. Load the session from the store; 404 if absent.
        4. Verify the session belongs to the caller; 403 otherwise.
        5. Load the graph; 404 if none exists yet.
        6. Return the full graph JSON.
        """
        if not self._settings.graph_enabled:
            raise HTTPException(status_code=404, detail="Graph feature disabled")

        user = self._get_current_user(request)

        session = await self._store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        if session.user_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")

        graph = await self._service.get(session_id)
        if graph is None:
            raise HTTPException(status_code=404, detail="No graph for this session")

        return graph.model_dump(mode="json")
