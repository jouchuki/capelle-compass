"""
Tests for the email-verification gate (compass-email-verification).

Drives :class:`AuthHandler` directly with a real :class:`JWTAuthenticator` +
:class:`EmailVerificationService`, a fake in-memory store (the handler only
touches four user methods), and a capturing email sender. Postgres-specific
persistence is out of scope here — the store double stands in for it, so the
suite stays on the SQLite-free fast path.

Covered:
* gate active → register issues NO session, emails a link, user is unverified;
* login before verifying → 403 with the ``email_not_verified`` code;
* following the link → user verified, session cookie minted, redirect to app;
* login after verifying → success;
* resend re-emails and always returns ok (no account enumeration);
* an invalid/expired token bounces to /login?verified=invalid;
* SSO accounts are exempt from the gate even when unverified;
* gate inactive (default settings) → legacy auto-login, /verify route is 404.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from capelle_platform.auth.email_verification import EmailVerificationService
from capelle_platform.auth.impl_jwt import JWTAuthenticator
from capelle_platform.email.base_sender import BaseEmailSender
from capelle_platform.handlers.auth import (
    EMAIL_NOT_VERIFIED_CODE,
    AuthHandler,
    ResendVerificationRequest,
)
from capelle_platform.models.user import User, UserCreate

_HOST = "compass.example"


class _FakeStore:
    """In-memory stand-in exercising only the methods AuthHandler calls."""

    def __init__(self) -> None:
        self.by_id: dict[str, User] = {}

    async def get_user_by_email(self, email: str) -> User | None:
        for u in self.by_id.values():
            if u.email == email:
                return u
        return None

    async def get_user_by_id(self, user_id: str) -> User | None:
        return self.by_id.get(user_id)

    async def create_user(self, user: User) -> User:
        if any(u.email == user.email for u in self.by_id.values()):
            raise ValueError("Email already registered")
        self.by_id[user.id] = user
        return user

    async def set_email_verified(self, user_id: str) -> None:
        user = self.by_id[user_id]
        self.by_id[user_id] = user.model_copy(update={"email_verified": True})


class _CapturingSender(BaseEmailSender):
    """Email sender double that records every message instead of sending."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    async def send(
        self, *, to: str, subject: str, html: str, text: str | None = None
    ) -> None:
        self.sent.append({"to": to, "subject": subject, "html": html})


def _request(host: str = _HOST) -> Request:
    """Minimal ASGI request carrying the given Host header (compass by default)."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/register",
        "headers": [(b"host", host.encode("latin-1"))],
        "query_string": b"",
    }
    return Request(scope)


def _handler(settings, *, active: bool) -> tuple[AuthHandler, _FakeStore, _CapturingSender]:
    store = _FakeStore()
    sender = _CapturingSender()
    handler = AuthHandler(
        JWTAuthenticator(settings),
        store,  # type: ignore[arg-type] — duck-typed store double
        settings,
        email_sender=sender,
        verification_service=EmailVerificationService(settings),
    )
    assert handler._verification_active is active
    return handler, store, sender


def _link_token(sender: _CapturingSender) -> str:
    """Extract the verification token from the most recent email."""
    html = sender.sent[-1]["html"]
    marker = "/api/auth/verify?token="
    start = html.index(marker) + len(marker)
    end = html.index('"', start)
    return html[start:end]


@pytest.mark.asyncio
async def test_register_when_active_emails_link_and_issues_no_session(
    settings_factory,
) -> None:
    settings = settings_factory(email_verification_enabled=True)
    handler, store, sender = _handler(settings, active=True)

    resp = Response()
    result = await handler.register(
        UserCreate(email="new@test.com", password="hunter2"), _request(), resp
    )

    assert result == {"status": "verification_required", "email": "new@test.com"}
    assert resp.status_code == 202
    # No session cookie was set.
    assert "set-cookie" not in {k.lower() for k, _ in resp.raw_headers}
    # Exactly one email, to the registrant.
    assert len(sender.sent) == 1 and sender.sent[0]["to"] == "new@test.com"
    # Persisted user is unverified.
    user = await store.get_user_by_email("new@test.com")
    assert user is not None and user.email_verified is False


@pytest.mark.asyncio
async def test_login_blocked_until_verified_then_allowed(
    settings_factory,
) -> None:
    settings = settings_factory(email_verification_enabled=True)
    handler, _store, sender = _handler(settings, active=True)
    await handler.register(
        UserCreate(email="u@test.com", password="hunter2"), _request(), Response()
    )

    # Before verifying: 403 with the machine-readable code.
    with pytest.raises(HTTPException) as exc:
        await handler.login(
            UserCreate(email="u@test.com", password="hunter2"),
            _request(),
            Response(),
        )
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == EMAIL_NOT_VERIFIED_CODE

    # Follow the link.
    token = _link_token(sender)
    redirect = await handler.verify(token, _request())
    assert isinstance(redirect, RedirectResponse)
    assert redirect.status_code == 303
    assert redirect.headers["location"].endswith("/?verified=1")
    assert settings.auth_cookie_name in redirect.headers.get("set-cookie", "")

    # After verifying: login succeeds and mints a session.
    ok_resp = Response()
    token_resp = await handler.login(
        UserCreate(email="u@test.com", password="hunter2"), _request(), ok_resp
    )
    assert token_resp.email == "u@test.com"
    assert settings.auth_cookie_name in (ok_resp.headers.get("set-cookie") or "")


@pytest.mark.asyncio
async def test_resend_is_ok_and_reemails_for_unverified(
    settings_factory,
) -> None:
    settings = settings_factory(email_verification_enabled=True)
    handler, _store, sender = _handler(settings, active=True)
    await handler.register(
        UserCreate(email="u@test.com", password="hunter2"), _request(), Response()
    )
    assert len(sender.sent) == 1

    out = await handler.resend_verification(
        ResendVerificationRequest(email="u@test.com"), _request()
    )
    assert out == {"ok": True}
    assert len(sender.sent) == 2

    # Unknown address: still ok, no email (no enumeration leak).
    out2 = await handler.resend_verification(
        ResendVerificationRequest(email="ghost@test.com"), _request()
    )
    assert out2 == {"ok": True}
    assert len(sender.sent) == 2


@pytest.mark.asyncio
async def test_invalid_token_bounces_to_login(settings_factory) -> None:
    settings = settings_factory(email_verification_enabled=True)
    handler, _store, _sender = _handler(settings, active=True)
    redirect = await handler.verify("not-a-real-token", _request())
    assert isinstance(redirect, RedirectResponse)
    assert redirect.headers["location"].endswith("/login?verified=invalid")
    assert "set-cookie" not in {k.lower() for k, _ in redirect.raw_headers}


@pytest.mark.asyncio
async def test_sso_account_is_exempt_from_gate(settings_factory) -> None:
    settings = settings_factory(email_verification_enabled=True)
    handler, store, _sender = _handler(settings, active=True)
    # An SSO account with an empty password hash, unverified flag, sso source.
    sso = User(
        id="sso-1",
        email="sso@test.com",
        hashed_password=JWTAuthenticator(settings).hash_password("ssopassword"),
        auth_source="sso",
        email_verified=False,
    )
    await store.create_user(sso)
    # Login is allowed despite email_verified=False because auth_source==sso.
    resp = Response()
    token = await handler.login(
        UserCreate(email="sso@test.com", password="ssopassword"), _request(), resp
    )
    assert token.email == "sso@test.com"


@pytest.mark.asyncio
async def test_verification_enabled_applies_on_every_hostname(
    settings_factory,
) -> None:
    # Verification applies to all configured application hostnames.
    settings = settings_factory(email_verification_enabled=True)
    handler, _store, sender = _handler(settings, active=True)

    resp = Response()
    result = await handler.register(
        UserCreate(email="capelle@test.com", password="hunter2"),
        _request(host="data-compass.org"),
        resp,
    )
    # A different hostname cannot bypass this deployment's verification gate.
    assert result["status"] == "verification_required"
    assert "set-cookie" not in resp.headers
    assert len(sender.sent) == 1



@pytest.mark.asyncio
async def test_gate_inactive_is_legacy_autologin(settings_factory) -> None:
    # Default settings → email_verification_enabled is False → gate dormant.
    settings = settings_factory()
    handler, _store, sender = _handler(settings, active=False)

    resp = Response()
    result = await handler.register(
        UserCreate(email="legacy@test.com", password="hunter2"), _request(), resp
    )
    # Legacy behaviour: a real token + session cookie, no verification email.
    assert result.email == "legacy@test.com"  # TokenResponse
    assert settings.auth_cookie_name in (resp.headers.get("set-cookie") or "")
    assert sender.sent == []

    # The /verify route is dormant (404) when the gate is off.
    with pytest.raises(HTTPException) as exc:
        await handler.verify("anything", _request())
    assert exc.value.status_code == 404
