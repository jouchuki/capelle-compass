"""Public API for the authentication module."""

from capelle_platform.auth.base_authenticator import BaseAuthenticator
from capelle_platform.auth.impl_jwt import JWTAuthenticator

__all__: list[str] = ["BaseAuthenticator", "JWTAuthenticator"]
