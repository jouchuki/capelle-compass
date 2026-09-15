"""
Abstract contract for the WebSocket broadcast hub.

Separating the interface from the implementation lets the Builder
swap between an in-memory hub (single-instance dev) and a
RabbitMQ-backed hub (multi-instance prod) without any handler needing
to know which is live.

Thread-safety and event-loop safety are mechanics owned by the
implementation; the contract only guarantees "what" not "how".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from fastapi import WebSocket


class BaseWebSocketHub(ABC):
    """
    Per-user WebSocket broadcast surface.

    A user may have multiple browser tabs open; every tab registers
    its own connection via :meth:`connect`. Broadcast reaches every
    tab owned by the given ``user_id`` — potentially across multiple
    backend instances when the hub has a cross-instance fanout.
    """

    @abstractmethod
    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        """
        Accept and register a new connection for ``user_id``.

        Implementations MUST call ``await websocket.accept()`` before
        returning so the handshake completes before the caller enters
        its receive loop.
        """

    @abstractmethod
    async def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        """Deregister a connection. Idempotent: safe to call twice."""

    @abstractmethod
    async def broadcast_to_user(
        self, user_id: str, data: dict[str, Any]
    ) -> None:
        """
        Fan ``data`` out to every connection owned by ``user_id``.

        Implementations MUST be resilient to dead connections (silently
        drop them) and to network failures in a cross-instance fanout
        (log + continue; never raise out of this method).
        """

    @abstractmethod
    async def broadcast_all(self, data: dict[str, Any]) -> None:
        """Send ``data`` to every connected user. Rarely used."""

    async def start(self) -> None:
        """
        Optional lifecycle hook for hubs that need async bootstrap.

        The in-memory hub is pure data — it can skip. The RabbitMQ
        hub connects to the broker here. The default implementation
        is a no-op so the lifespan caller never has to branch.
        """

    async def stop(self) -> None:
        """Optional lifecycle hook for graceful shutdown."""
