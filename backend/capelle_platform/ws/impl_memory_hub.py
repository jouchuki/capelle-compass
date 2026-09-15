"""
Single-instance in-memory implementation of :class:`BaseWebSocketHub`.

All state lives in a plain dict behind an ``asyncio.Lock``. Good for
local dev and the single-yuta MVP. When we scale out to multiple
yutas, swap the Builder to :class:`RabbitMQWebSocketHub` — this file
stays untouched as the fallback.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket

from capelle_platform.observability import get_logger
from capelle_platform.ws.base_hub import BaseWebSocketHub

_logger = get_logger(__name__)


class InMemoryWebSocketHub(BaseWebSocketHub):
    """
    Process-local WebSocket broadcast hub.

    A single user may have multiple tabs open; each gets its own
    connection. Messages broadcast to this hub reach only the tabs
    connected to THIS backend instance — cross-instance fanout is the
    RabbitMQ hub's job.
    """

    def __init__(self) -> None:
        """Initialise with an empty connection registry."""
        self._connections: dict[str, list[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        """Accept the handshake and add the connection to the registry."""
        await websocket.accept()
        async with self._lock:
            self._connections.setdefault(user_id, []).append(websocket)
        _logger.info("ws_connected", user_id=user_id)

    async def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        """Remove a closed connection. Idempotent."""
        async with self._lock:
            conns = self._connections.get(user_id, [])
            if websocket in conns:
                conns.remove(websocket)
            if not conns:
                self._connections.pop(user_id, None)
        _logger.info("ws_disconnected", user_id=user_id)

    async def broadcast_to_user(
        self, user_id: str, data: dict[str, Any]
    ) -> None:
        """
        Serialise ``data`` once and send to every connection for ``user_id``.

        Dead connections encountered during the send are evicted under
        the lock after the fan-out completes so a slow client cannot
        block fast ones.
        """
        payload = json.dumps(data, default=str)
        dead: list[WebSocket] = []

        async with self._lock:
            conns = list(self._connections.get(user_id, []))

        for ws in conns:
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001 — dead sockets must not block siblings
                dead.append(ws)

        if dead:
            async with self._lock:
                conns = self._connections.get(user_id, [])
                for ws in dead:
                    if ws in conns:
                        conns.remove(ws)

    async def broadcast_all(self, data: dict[str, Any]) -> None:
        """Reach every connected user — rarely used, for announcements."""
        async with self._lock:
            user_ids = list(self._connections.keys())
        for uid in user_ids:
            await self.broadcast_to_user(uid, data)
