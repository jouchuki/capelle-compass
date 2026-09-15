"""
Tests for the transport/auth-layer audit remediations (API-2..API-8, STORE-3/7).

Covers the slices owned by the transport/auth agent:

* API-2 — CORS allow-list driven by settings (prod vs debug).
* API-3 — token revocation registry + WS revalidation honouring it.
* API-4 — ``validate_token`` raises ValueError (not KeyError) on missing claims.
* API-5 — internal-route HMAC signature verification (hook + ask).
* API-7 — ``X-Capelle-Hostname`` gated behind a flag (default off).
* STORE-3 / STORE-7 — Builder topology guards.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest

from capelle_platform.auth.impl_jwt import JWTAuthenticator
from capelle_platform.auth.revocation import InMemoryTokenRevocationRegistry
from capelle_platform.builder import AppBuilder
from capelle_platform.settings import Settings
from capelle_platform.utils.hook_auth import (
    sign_message_id,
    sign_timestamped,
    verify_hook_signature,
    verify_timestamped_signature,
)

_SECRET = "test-secret-minimum-32-characters-long-ok"
_BROKER = "amqp://capelle-app:realpw@kiyotaka-ijichi:5672/capelle"
_DSN = "postgresql://capelle-app:realpw@aoi-todo:5432/capelle"


def _settings(**overrides) -> Settings:
    overrides.setdefault("jwt_secret", _SECRET)
    return Settings(**overrides)


# --- API-4: missing-claim → ValueError, not KeyError ------------------------


def test_validate_token_missing_email_raises_value_error() -> None:
    auth = JWTAuthenticator(_settings())
    now = datetime.now(timezone.utc)
    token = pyjwt.encode(
        {"sub": "u1", "iat": now, "exp": now + timedelta(hours=1)},
        _SECRET,
        algorithm="HS256",
    )
    with pytest.raises(ValueError):
        auth.validate_token(token)


def test_validate_token_with_iat_returns_issued_at() -> None:
    auth = JWTAuthenticator(_settings())
    now = datetime.now(timezone.utc)
    token = pyjwt.encode(
        {"sub": "u1", "email": "a@b.c", "iat": now, "exp": now + timedelta(hours=1)},
        _SECRET,
        algorithm="HS256",
    )
    user, iat = auth.validate_token_with_iat(token)
    assert user.id == "u1"
    assert iat is not None


# --- API-3: revocation registry ---------------------------------------------


def test_revocation_registry_monotonic_and_iat_check() -> None:
    reg = InMemoryTokenRevocationRegistry()
    now = time.time()
    assert reg.is_revoked("u1", now) is False

    reg.revoke_before("u1", now)
    # A token issued at/before the revocation instant is revoked.
    assert reg.is_revoked("u1", now - 1) is True
    assert reg.is_revoked("u1", now) is True
    # A token minted strictly after stays valid.
    assert reg.is_revoked("u1", now + 10) is False
    # A token with no iat is treated as revoked once any revocation exists.
    assert reg.is_revoked("u1", None) is True
    # Other users are unaffected.
    assert reg.is_revoked("u2", now - 1) is False

    # Monotonic: an earlier instant must not weaken the revocation.
    reg.revoke_before("u1", now - 100)
    assert reg.is_revoked("u1", now) is True


# --- API-5: internal-route HMAC ---------------------------------------------


def test_hook_signature_roundtrip_and_disabled_noop() -> None:
    sig = sign_message_id("s3cr3t", "msg-1")
    assert verify_hook_signature("s3cr3t", "msg-1", sig) is True
    assert verify_hook_signature("s3cr3t", "msg-1", "bad") is False
    assert verify_hook_signature("s3cr3t", "msg-1", None) is False
    # Empty secret disables verification (loopback guard only).
    assert verify_hook_signature("", "msg-1", None) is True


def test_timestamped_signature_rejects_stale_and_tampered() -> None:
    ts = str(int(time.time()))
    body = b'{"question":"hi"}'
    sig = sign_timestamped("s3cr3t", ts, body)
    assert verify_timestamped_signature("s3cr3t", ts, body, sig) is True
    # Tampered body.
    assert verify_timestamped_signature("s3cr3t", ts, b'{"question":"x"}', sig) is False
    # Stale timestamp.
    old = str(int(time.time()) - 10_000)
    assert verify_timestamped_signature("s3cr3t", old, body, sign_timestamped("s3cr3t", old, body)) is False
    # Disabled.
    assert verify_timestamped_signature("", None, body, None) is True


# --- API-2: CORS allow-list --------------------------------------------------


def test_cors_origins_prod_excludes_dev() -> None:
    s = _settings(public_origin="https://capelle.example/", debug=False)
    origins = s.cors_allowed_origins
    # The public origin is always trusted; the local Vite dev origins are
    # excluded in prod so they are never a credentialed origin there.
    assert origins[0] == "https://capelle.example"
    assert "http://localhost:5173" not in origins
    assert "http://127.0.0.1:5173" not in origins
    # The Office add-in origin is trusted in prod too (Bearer-auth, not
    # cookie) — see test_addin_ms_ecosystem for the full add-in contract.
    assert origins == ["https://capelle.example"]


def test_cors_origins_debug_includes_vite() -> None:
    s = _settings(public_origin="https://capelle.example", debug=True)
    assert "http://localhost:5173" in s.cors_allowed_origins
    assert "https://capelle.example" in s.cors_allowed_origins


# --- API-7: hostname header gated --------------------------------------------


def test_hostname_header_flag_defaults_off() -> None:
    assert _settings().expose_hostname_header is False


# --- STORE-3 / STORE-7: Builder topology guards ------------------------------


def test_builder_rejects_sqlite_with_concurrent_queue() -> None:
    settings = _settings(queue="rabbitmq", rabbitmq_url=_BROKER, store="sqlite")
    with pytest.raises(ValueError, match="sqlite"):
        AppBuilder(settings).build()


def test_builder_rejects_memory_queue_in_scaleout() -> None:
    settings = _settings(queue="memory", ws_hub="rabbitmq", rabbitmq_url=_BROKER)
    with pytest.raises(ValueError, match="dev/single-process"):
        AppBuilder(settings).build()


def test_builder_memory_queue_sqlite_dev_ok() -> None:
    # The default single-process dev topology must still build.
    app = AppBuilder(_settings()).build()
    assert app is not None
