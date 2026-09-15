"""
User domain models.

Separates the wire format (UserCreate) from the persisted entity (User)
and the auth response envelope (TokenResponse).
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    """Payload for user registration — only what the client must supply."""

    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class User(BaseModel):
    """
    Persisted user entity returned by the store layer.

    ``daily_analysis_limit`` is a per-account override of
    :attr:`Settings.daily_analysis_limit`. Semantics:

    * ``None``  — no override; the quota enforcer falls back to the
      global default (see :class:`DailyAnalysisQuotaEnforcer`).
    * ``0``     — **unlimited**; interpreted as "no cap at all" by the
      enforcer. Reserved for heavy users / admin operators.
    * positive  — user-specific hard cap for the calendar day.

    The field is admin-only: public endpoints (login, /auth/me) strip
    it before serialising so normal users never see each other's or
    even their own override number leak through the JWT/cookie flow.
    """

    id: str
    email: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    hashed_password: str = ""
    daily_analysis_limit: int | None = None
    # --- Entra/Azure AD OIDC SSO linkage (compass-entra-oidc-sso) ---
    #: Entra ``oid`` claim — the stable per-user object id within a tenant.
    #: ``None`` for password-only accounts. Paired with :attr:`entra_tid`
    #: it forms the durable re-linkage key when an SSO user's email changes.
    entra_oid: str | None = None
    #: Entra ``tid`` claim — the user's home tenant id. ``None`` for
    #: password-only accounts.
    entra_tid: str | None = None
    #: How the account is authenticated. ``"password"`` for legacy
    #: email+password accounts (the default backfilled onto every pre-SSO
    #: row during migration); ``"sso"`` for accounts provisioned or linked
    #: via Entra OIDC.
    auth_source: str = "password"
    #: Whether the account's email address has been confirmed via the
    #: verification link. New password accounts start ``False`` and must
    #: verify before they can log in (see :class:`AuthHandler`). SSO accounts
    #: are provisioned ``True`` (the identity provider already verified the
    #: address), and every account that predates this feature is grandfathered
    #: to ``True`` by the store migration. Default ``False`` so a freshly
    #: constructed password user is unverified until proven otherwise.
    email_verified: bool = False

    model_config: dict[str, object] = {"from_attributes": True}


class TokenResponse(BaseModel):
    """JWT token envelope returned after successful authentication."""

    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
