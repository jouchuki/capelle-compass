"""
Back-compat shim.

The hub used to be a single concrete class named ``WebSocketHub``
living in this file. It was split into :class:`BaseWebSocketHub`
(ABC) + :class:`InMemoryWebSocketHub` (single-process impl) to make
room for a RabbitMQ-backed fanout variant used in multi-yuta
deployments. This file re-exports the old name so any import path
``from capelle_platform.ws.hub import WebSocketHub`` keeps working.
"""

from capelle_platform.ws.base_hub import BaseWebSocketHub
from capelle_platform.ws.impl_memory_hub import InMemoryWebSocketHub

# Old callers imported the concrete class under this name — keep the
# alias pointing at the in-memory impl so single-instance deployments
# require zero churn.
WebSocketHub = InMemoryWebSocketHub

__all__ = ["BaseWebSocketHub", "InMemoryWebSocketHub", "WebSocketHub"]
