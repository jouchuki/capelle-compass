"""
WebSocket handler.

Upgrades HTTP connections to WebSocket for real-time progress updates.
Authenticates via the HttpOnly auth cookie when sent by the browser, or
via a query-parameter token for test clients. Re-validates the token
periodically so an expired JWT doesn't keep receiving broadcasts, and
checks a per-user revocation registry so a logout/forced-logout kills the
live socket before the token's natural expiry (API-3).
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from capelle_platform.auth.base_authenticator import BaseAuthenticator
from capelle_platform.auth.revocation import BaseTokenRevocationRegistry
from capelle_platform.observability import get_logger
from capelle_platform.settings import Settings
from capelle_platform.ws.hub import WebSocketHub

_logger = get_logger(__name__)

_REVALIDATE_INTERVAL_SECONDS = 60


class WSHandler:
    """
    Handler for WebSocket connections.

    Each connection is authenticated via JWT token (cookie preferred,
    query fallback) and registered with the WebSocketHub for the user's
    ID. The hub pushes analysis progress events to all of a user's
    connections. A background task re-validates the token every 60s and
    closes the connection with code 4401 if the token has expired.
    """

    def __init__(
        self,
        authenticator: BaseAuthenticator,
        ws_hub: WebSocketHub,
        settings: Settings,
        revocation: BaseTokenRevocationRegistry | None = None,
    ) -> None:
        """
        Construct with auth, hub, and settings dependencies.

        Args:
            authenticator: Validates the JWT on connect and on re-check.
            ws_hub: Connection registry for broadcasting events.
            settings: Application settings (cookie name).
            revocation: Per-user token revocation registry (API-3). When
                supplied, the connect path and the revalidation loop close a
                socket whose token has been revoked (logout) even before its
                natural expiry. ``None`` keeps the legacy expiry-only
                behaviour.
        """
        self._auth = authenticator
        self._hub = ws_hub
        self._settings = settings
        self._revocation = revocation
        self.router = APIRouter(tags=["websocket"])
        self.router.add_api_websocket_route("/api/ws", self.websocket_endpoint)

    def _extract_token(self, websocket: WebSocket, query_token: str) -> str:
        """
        Pick the auth token off the cookie, then fall back to the query string.

        Browsers send cookies on WS upgrade requests, so the cookie is
        the canonical source. The query-string form remains for CLI/test
        clients that can't easily set cookies.
        """
        cookie_token = websocket.cookies.get(self._settings.auth_cookie_name, "")
        return cookie_token or query_token

    async def websocket_endpoint(
        self,
        websocket: WebSocket,
        token: str = Query(default=""),
    ) -> None:
        """
        Accept a WebSocket connection, authenticate, and listen until disconnect.

        The server pushes events; the client only needs to stay connected.
        Any message from the client is logged but otherwise ignored.
        """
        resolved = self._extract_token(websocket, token)
        if not resolved:
            await websocket.close(code=4001, reason="Missing token")
            return

        try:
            user, iat_epoch = self._auth.validate_token_with_iat(resolved)
        except ValueError as exc:
            await websocket.close(code=4001, reason=str(exc))
            return

        # Reject a token that was already revoked (e.g. the user logged out in
        # another tab before opening this socket) — API-3.
        if self._is_revoked(user.id, iat_epoch):
            await websocket.close(code=4401, reason="Token revoked")
            return

        await self._hub.connect(user.id, websocket)
        revalidator = asyncio.create_task(
            self._revalidate_loop(websocket, resolved, user.id, iat_epoch),
            name=f"ws-revalidate-{user.id}",
        )
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            revalidator.cancel()
            await self._hub.disconnect(user.id, websocket)

    def _is_revoked(self, user_id: str, iat_epoch: float | None) -> bool:
        """Return whether the token is revoked per the registry (no-op if none)."""
        if self._revocation is None:
            return False
        return self._revocation.is_revoked(user_id, iat_epoch)

    async def _revalidate_loop(
        self,
        websocket: WebSocket,
        token: str,
        user_id: str,
        iat_epoch: float | None,
    ) -> None:
        """
        Re-check the JWT every minute; close on natural expiry OR revocation.

        Re-validating the captured token catches natural ``exp`` (the token's
        signature is fixed), and the revocation check catches a logout /
        forced-logout that happened after connect — the captured token's
        ``exp`` alone cannot see that (API-3). Uses close code 4401 so the
        frontend can distinguish a forced auth-logout from other disconnects
        and redirect to /login.
        """
        try:
            while True:
                await asyncio.sleep(_REVALIDATE_INTERVAL_SECONDS)
                try:
                    self._auth.validate_token(token)
                except ValueError as exc:
                    _logger.info(
                        "ws_token_expired",
                        reason=str(exc),
                    )
                    await self._close_quietly(websocket, "Token expired")
                    return
                if self._is_revoked(user_id, iat_epoch):
                    _logger.info("ws_token_revoked", user_id=user_id)
                    await self._close_quietly(websocket, "Token revoked")
                    return
        except asyncio.CancelledError:
            return

    @staticmethod
    async def _close_quietly(websocket: WebSocket, reason: str) -> None:
        """Close the socket with code 4401, swallowing already-closing errors."""
        try:
            await websocket.close(code=4401, reason=reason)
        except Exception:  # noqa: BLE001 — socket may already be closing
            pass
