"""
Abstract base class defining the authentication contract.

Every auth implementation (JWT, Supabase, OAuth) must honour this interface.
Decision layer (handlers) depends only on this ABC — never on a concrete impl.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from capelle_platform.models.user import TokenResponse, User


class BaseAuthenticator(ABC):
    """
    Contract for authentication and token management.

    Implementations handle password hashing, token issuance, and
    token validation.  The rest of the platform sees only this interface.
    """

    @abstractmethod
    def hash_password(self, raw_password: str) -> str:
        """
        Produce a secure hash of a plaintext password.

        The hash must be verifiable via verify_password.
        """

    @abstractmethod
    def verify_password(self, raw_password: str, hashed: str) -> bool:
        """
        Check a plaintext password against its stored hash.

        Returns True on match, False otherwise.  Must be constant-time.
        """

    @abstractmethod
    def create_token(self, user: User) -> TokenResponse:
        """
        Issue a signed token for an authenticated user.

        The returned TokenResponse includes the encoded JWT and user metadata.
        """

    @abstractmethod
    def validate_token(self, token: str) -> User:
        """
        Decode and validate a bearer token.

        Returns the User embedded in the token claims.
        Raises ValueError if the token is expired, malformed, or invalid.
        """

    @abstractmethod
    def validate_token_with_iat(self, token: str) -> tuple[User, float | None]:
        """
        Decode and validate a bearer token, returning its issued-at instant.

        Like :meth:`validate_token` but also surfaces the token's ``iat``
        claim (unix seconds, or ``None`` if absent) so a revocation registry
        can decide whether the token pre-dates a logout (API-3).

        Returns a ``(User, iat_epoch)`` tuple.
        Raises ValueError if the token is expired, malformed, or invalid.
        """
