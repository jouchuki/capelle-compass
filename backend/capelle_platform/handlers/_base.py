"""
Shared handler helpers — token extraction and admin gating.

Kept as a mixin class so the newer handlers (telemetry, admin) avoid
copy-pasting the same eight lines the legacy handlers carry. The
legacy handlers were left intact when cookie-auth shipped; when they
grow another reason to change we fold them onto this base.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from capelle_platform.auth.base_authenticator import BaseAuthenticator
from capelle_platform.models.user import User
from capelle_platform.settings import Settings


class AuthenticatedHandlerMixin:
    """
    Cookie-or-bearer token extraction + admin allowlist check.

    Subclasses are expected to store ``_auth`` (``BaseAuthenticator``)
    and ``_settings`` (``Settings``) on ``self``; the mixin only reads
    those two attributes.
    """

    _auth: BaseAuthenticator
    _settings: Settings

    def _get_current_user(self, request: Request) -> User:
        """
        Extract the user from the HttpOnly cookie or a bearer header.

        The cookie is the canonical credential for browser traffic and
        is set on login/register; bearer tokens remain supported so
        scripts and tests can hit the API without a browser.
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

    def _require_admin(self, user: User) -> None:
        """
        Raise ``403 Forbidden`` unless the user's email is on the
        configured admin allowlist.

        The comparison is case-insensitive; the allowlist is frozen
        at settings parse time so no runtime mutation is possible.
        """
        if user.email.strip().lower() not in self._settings.admin_email_set:
            raise HTTPException(status_code=403, detail="Admin access required")
