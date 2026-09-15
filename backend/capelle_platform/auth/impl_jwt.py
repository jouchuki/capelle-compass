"""
JWT-based authentication implementation.

Uses PyJWT for token signing/verification and bcrypt for password hashing.
Stateless — all user identity is embedded in the token claims.
Swappable for Supabase auth by implementing the same BaseAuthenticator ABC.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from capelle_platform.auth.base_authenticator import BaseAuthenticator
from capelle_platform.models.user import TokenResponse, User
from capelle_platform.observability import get_logger
from capelle_platform.settings import Settings

_logger = get_logger(__name__)


class JWTAuthenticator(BaseAuthenticator):
    """
    Production-grade JWT authentication with bcrypt password hashing.

    Initialised once by the Builder with the application Settings.
    All parameters (secret, algorithm, expiry) come from Settings — no
    magic numbers in this class.
    """

    def __init__(self, settings: Settings) -> None:
        """
        Construct the authenticator from application settings.

        Args:
            settings: Central config providing JWT secret, algorithm, and expiry.
        """
        self._secret = settings.jwt_secret
        self._algorithm = settings.jwt_algorithm
        self._expiry_minutes = settings.jwt_expiry_minutes

    def hash_password(self, raw_password: str) -> str:
        """
        Hash a password using bcrypt with automatic salt generation.

        Returns the hashed password as a UTF-8 string for storage.
        """
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(raw_password.encode("utf-8"), salt)
        return hashed.decode("utf-8")

    def verify_password(self, raw_password: str, hashed: str) -> bool:
        """
        Constant-time comparison of a plaintext password against a bcrypt hash.

        Returns True if the password matches, False otherwise.
        """
        try:
            return bcrypt.checkpw(
                raw_password.encode("utf-8"), hashed.encode("utf-8")
            )
        except Exception:
            _logger.warning("password_verify_failed")
            return False

    def create_token(self, user: User) -> TokenResponse:
        """
        Issue a signed JWT containing user identity claims.

        The token embeds user_id and email, with an expiration timestamp.
        """
        now = datetime.now(timezone.utc)
        payload = {
            "sub": user.id,
            "email": user.email,
            "iat": now,
            "exp": now + timedelta(minutes=self._expiry_minutes),
        }
        encoded = jwt.encode(payload, self._secret, algorithm=self._algorithm)
        _logger.info("token_issued", user_id=user.id)
        return TokenResponse(
            access_token=encoded,
            token_type="bearer",
            user_id=user.id,
            email=user.email,
        )

    def validate_token(self, token: str) -> User:
        """
        Decode a JWT and return the embedded User.

        Raises ValueError if the token is expired, malformed, or tampered with.
        """
        user, _ = self.validate_token_with_iat(token)
        return user

    def validate_token_with_iat(self, token: str) -> tuple[User, float | None]:
        """
        Decode a JWT and return ``(User, iat_epoch)``.

        ``iat_epoch`` is the token's issued-at as unix seconds, or ``None`` if
        the claim is absent. Used by the WS revocation check (API-3).
        Raises ValueError if the token is expired, malformed, or tampered with.
        """
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                # Require the identity claims we subscript below. Without this,
                # a validly-signed-but-malformed token missing "sub"/"email"
                # raised KeyError on the dict access → unhandled 500 instead of
                # the intended 401 (API-4). PyJWT now raises InvalidTokenError,
                # which the except below maps to ValueError → 401.
                options={"require": ["sub", "email", "exp"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise ValueError("Token has expired") from exc
        except jwt.InvalidTokenError as exc:
            raise ValueError(f"Invalid token: {exc}") from exc

        iat_raw = payload.get("iat")
        iat_epoch: float | None
        try:
            iat_epoch = float(iat_raw) if iat_raw is not None else None
        except (TypeError, ValueError):
            iat_epoch = None

        user = User(
            id=payload["sub"],
            email=payload["email"],
        )
        return user, iat_epoch
