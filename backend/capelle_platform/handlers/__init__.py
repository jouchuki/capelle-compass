"""Public API for HTTP/WS handlers."""

from capelle_platform.handlers.auth import AuthHandler
from capelle_platform.handlers.chat import ChatHandler
from capelle_platform.handlers.health import HealthHandler
from capelle_platform.handlers.internal import InternalHandler
from capelle_platform.handlers.oidc import OidcDiscoveryClient, OidcHandler
from capelle_platform.handlers.ws import WSHandler

__all__: list[str] = [
    "AuthHandler",
    "ChatHandler",
    "HealthHandler",
    "InternalHandler",
    "OidcDiscoveryClient",
    "OidcHandler",
    "WSHandler",
]
