"""
Email-verification token service.

Mints and validates the single-purpose token carried in a verification link.
It is a JWT signed with the same HMAC secret as the session token (so no new
key material or DB table), but scoped with a ``purpose`` claim so a session
token can never be replayed as a verification token or vice-versa. Stateless:
the user id rides in ``sub`` and the link self-expires via ``exp``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt

from capelle_platform.observability import get_logger
from capelle_platform.settings import Settings

_logger = get_logger(__name__)

#: Distinguishes a verification token from a session token signed with the
#: same secret. Validation rejects any token whose ``purpose`` differs.
_PURPOSE = "email_verify"


class EmailVerificationService:
    """Issue and verify email-confirmation link tokens."""

    def __init__(self, settings: Settings) -> None:
        """Capture signing material + TTL from settings."""
        self._secret = settings.jwt_secret
        self._algorithm = settings.jwt_algorithm
        self._ttl_minutes = settings.email_verification_ttl_minutes

    def issue(self, user_id: str) -> str:
        """Return a signed, time-boxed verification token for ``user_id``."""
        now = datetime.now(timezone.utc)
        payload = {
            "sub": user_id,
            "purpose": _PURPOSE,
            "iat": now,
            "exp": now + timedelta(minutes=self._ttl_minutes),
        }
        return jwt.encode(payload, self._secret, algorithm=self._algorithm)

    def verify(self, token: str) -> str:
        """
        Validate ``token`` and return the ``user_id`` it confirms.

        Raises:
            ValueError: if the token is expired, tampered with, missing the
                required claims, or is not a verification-purpose token.
        """
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                options={"require": ["sub", "exp", "purpose"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise ValueError("Verification link has expired") from exc
        except jwt.InvalidTokenError as exc:
            raise ValueError(f"Invalid verification link: {exc}") from exc

        if payload.get("purpose") != _PURPOSE:
            raise ValueError("Token is not an email-verification token")
        return str(payload["sub"])
