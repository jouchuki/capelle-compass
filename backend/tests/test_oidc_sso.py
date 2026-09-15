"""
Tests for the Entra/Azure AD OIDC SSO backend (compass-entra-oidc-sso).

A self-contained **mock IdP** drives the whole flow without network IO:

* a locally-generated RS256 key pair signs id_tokens; its public half is
  served as a JWK so the handler's signature check passes;
* an :class:`httpx.MockTransport` serves the discovery document, the JWKS,
  and the token endpoint, and is injected into the
  :class:`OidcDiscoveryClient` the handler uses.

Coverage (per the design spec):

* callback links an existing user by email (and persists the oid/tid key);
* callback auto-provisions a new user (empty password, entra_oid/tid set,
  auth_source='sso'), tagged by the request Host;
* callback rejects a bad ``state``, a bad signature, an ``aud`` mismatch,
  and an expired token — always with the generic SSO-error redirect;
* a happy-path callback sets the Compass auth cookie and 302s to the return
  path;
* ``/api/auth/config`` reflects ``sso_enabled`` only when both client id +
  secret are set, and the OIDC routes 404 when SSO is disabled;
* the users-table migration adds the columns and defaults legacy rows.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import httpx
import jwt
import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from cryptography.hazmat.primitives.asymmetric import rsa

from capelle_platform.auth.impl_jwt import JWTAuthenticator
from capelle_platform.handlers.auth import AuthHandler
from capelle_platform.handlers.oidc import OidcDiscoveryClient, OidcHandler
from capelle_platform.models.user import User
from capelle_platform.settings import Settings
from capelle_platform.store.impl_sqlite import SQLiteStore
from capelle_platform.utils import generate_id

_TENANT = "organizations"
_CLIENT_ID = "test-client-id"
_CLIENT_SECRET = "test-client-secret"
_KID = "test-kid"
_AUTHORITY = "https://login.microsoftonline.com"
_ISSUER = f"{_AUTHORITY}/test-tenant-id/v2.0"
_REDIRECT = "https://compass.example/api/auth/oidc/callback"
_JWT_SECRET = "test-secret-minimum-32-characters-long-ok"


# ----------------------------------------------------------------------
# Mock IdP key material + transport
# ----------------------------------------------------------------------


class _MockIdP:
    """Local RS256 IdP: signs id_tokens and serves discovery/JWKS/token."""

    def __init__(self) -> None:
        self._key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        jwk = json.loads(RSAAlgorithm.to_jwk(self._key.public_key()))
        jwk.update({"kid": _KID, "alg": "RS256", "use": "sig"})
        self._jwk = jwk
        #: Override the code→id_token map so a single test can stage a
        #: tampered/expired token for a known code.
        self.id_token_for_code: dict[str, str] = {}

    def sign(self, claims: dict[str, Any], *, key: Any | None = None) -> str:
        """Sign ``claims`` with the IdP key (or a foreign key for tests)."""
        return jwt.encode(
            claims,
            key or self._key,
            algorithm="RS256",
            headers={"kid": _KID},
        )

    def valid_claims(
        self,
        *,
        email: str = "user@example.com",
        oid: str = "oid-123",
        tid: str = "tid-456",
        nonce: str = "",
        aud: str = _CLIENT_ID,
        exp_offset: int = 300,
    ) -> dict[str, Any]:
        """Build a standard set of id_token claims."""
        return {
            "iss": _ISSUER,
            "aud": aud,
            "exp": int(time.time()) + exp_offset,
            "iat": int(time.time()),
            "nonce": nonce,
            "oid": oid,
            "tid": tid,
            "email": email,
            "name": "Test User",
        }

    def transport(self) -> httpx.MockTransport:
        """An httpx transport routing discovery/JWKS/token requests."""

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.endswith(".well-known/openid-configuration"):
                return httpx.Response(
                    200,
                    json={
                        "issuer": _ISSUER,
                        "jwks_uri": f"{_AUTHORITY}/{_TENANT}/discovery/v2.0/keys",
                        "token_endpoint": f"{_AUTHORITY}/{_TENANT}/oauth2/v2.0/token",
                        "authorization_endpoint": (
                            f"{_AUTHORITY}/{_TENANT}/oauth2/v2.0/authorize"
                        ),
                    },
                )
            if url.endswith("/discovery/v2.0/keys"):
                return httpx.Response(200, json={"keys": [self._jwk]})
            if url.endswith("/oauth2/v2.0/token"):
                body = request.content.decode("utf-8")
                form = dict(
                    pair.split("=", 1) for pair in body.split("&") if "=" in pair
                )
                code = form.get("code", "")
                id_token = self.id_token_for_code.get(code)
                if id_token is None:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(
                    200,
                    json={
                        "access_token": "ms-access",
                        "id_token": id_token,
                        "token_type": "Bearer",
                    },
                )
            return httpx.Response(404, json={"error": "not_found"})

        return httpx.MockTransport(handler)

    def foreign_key(self) -> Any:
        """A different RSA key — its signatures must fail validation."""
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)


# ----------------------------------------------------------------------
# App builder for the OIDC + auth routes over a real SQLite store
# ----------------------------------------------------------------------


def _make_settings(tmp_path, *, enabled: bool = True) -> Settings:
    """Settings with SSO toggled and an insecure-cookie (http test) flag."""
    overrides: dict[str, Any] = {
        "jwt_secret": _JWT_SECRET,
        "sqlite_path": tmp_path / f"oidc-{generate_id()}.db",
        "auth_cookie_secure": False,
        "oidc_tenant": _TENANT,
        "oidc_redirect_uri": _REDIRECT,
    }
    if enabled:
        overrides["oidc_client_id"] = _CLIENT_ID
        overrides["oidc_client_secret"] = _CLIENT_SECRET
    return Settings(**overrides)


def _build_app(settings: Settings, idp: _MockIdP) -> FastAPI:
    """
    Wire auth + OIDC handlers over a real store with the mock IdP injected.

    The store is initialised in the lifespan so the migration runs exactly
    as in production; TestClient drives that lifespan synchronously.
    """
    store = SQLiteStore(settings)
    authenticator = JWTAuthenticator(settings)
    auth_handler = AuthHandler(authenticator, store, settings)
    http_client = httpx.AsyncClient(transport=idp.transport())
    discovery = OidcDiscoveryClient(http_client, authority_base=_AUTHORITY)
    oidc_handler = OidcHandler(
        authenticator,
        store,
        settings,
        discovery,
        auth_handler._set_auth_cookie,
    )

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await store.initialize()
        yield
        await store.close()
        await http_client.aclose()

    app = FastAPI(lifespan=lifespan)
    app.include_router(auth_handler.router)
    if settings.oidc_enabled:
        app.include_router(oidc_handler.router)
    # Expose the store for assertions/seed inside tests.
    app.state.store = store
    return app


def _stage_login(client: TestClient) -> tuple[str, str, dict[str, str]]:
    """
    Run /login, returning ``(state, signed_state_cookie, cookies)``.

    Disables redirect-following so we can read the authorize ``state`` from
    the Location header and the signed state cookie from Set-Cookie.
    """
    resp = client.get(
        "/api/auth/oidc/login?return=/chat",
        follow_redirects=False,
        headers={"Host": "compass.example"},
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    # Extract state + nonce from the authorize URL query.
    from urllib.parse import parse_qs, urlparse

    qs = parse_qs(urlparse(location).query)
    state = qs["state"][0]
    nonce = qs["nonce"][0]
    cookie = resp.cookies.get("compass_oidc_state")
    assert cookie is not None
    return state, nonce, {"compass_oidc_state": cookie}


# ----------------------------------------------------------------------
# Migration tests (store-direct)
# ----------------------------------------------------------------------


async def test_migration_adds_columns_and_defaults_legacy_rows(
    tmp_path,
) -> None:
    """A pre-SSO users table gains the columns; legacy rows default."""
    db_path = tmp_path / "legacy.db"
    # Create a legacy users table WITHOUT the SSO columns and seed a row.
    conn = await aiosqlite.connect(str(db_path))
    await conn.execute(
        "CREATE TABLE users ("
        "id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, "
        "hashed_password TEXT NOT NULL, created_at TEXT NOT NULL, "
        "daily_analysis_limit INTEGER)"
    )
    await conn.execute(
        "INSERT INTO users (id, email, hashed_password, created_at) "
        "VALUES (?, ?, ?, ?)",
        ("legacy-1", "old@example.com", "hash", datetime.now(timezone.utc).isoformat()),
    )
    await conn.commit()
    await conn.close()

    settings = Settings(jwt_secret=_JWT_SECRET, sqlite_path=db_path)
    store = SQLiteStore(settings)
    await store.initialize()
    try:
        user = await store.get_user_by_email("old@example.com")
        assert user is not None
        # Legacy row defaulted correctly.
        assert user.entra_oid is None
        assert user.entra_tid is None
        assert user.auth_source == "password"

        # Columns are present + usable for an SSO write.
        sso = User(
            id=generate_id(),
            email="sso@example.com",
            hashed_password="",
            entra_oid="o1",
            entra_tid="t1",
            auth_source="sso",
        )
        await store.create_user(sso)
        found = await store.get_user_by_entra("o1", "t1")
        assert found is not None
        assert found.email == "sso@example.com"
        assert found.auth_source == "sso"
    finally:
        await store.close()


async def test_get_user_by_entra_requires_both_columns(tmp_path) -> None:
    """A partial (oid-only / tid-only) match resolves no user."""
    settings = Settings(
        jwt_secret=_JWT_SECRET, sqlite_path=tmp_path / "entra.db"
    )
    store = SQLiteStore(settings)
    await store.initialize()
    try:
        await store.create_user(
            User(
                id=generate_id(),
                email="x@example.com",
                hashed_password="",
                entra_oid="oid-A",
                entra_tid="tid-A",
                auth_source="sso",
            )
        )
        assert await store.get_user_by_entra("oid-A", "tid-A") is not None
        assert await store.get_user_by_entra("oid-A", "WRONG") is None
        assert await store.get_user_by_entra("WRONG", "tid-A") is None
    finally:
        await store.close()


# ----------------------------------------------------------------------
# Config endpoint + disabled-route tests
# ----------------------------------------------------------------------


def test_config_reports_enabled_only_when_credentials_set(tmp_path) -> None:
    """/api/auth/config flips on iff both client id + secret are set."""
    idp = _MockIdP()

    enabled = _make_settings(tmp_path, enabled=True)
    with TestClient(_build_app(enabled, idp)) as client:
        body = client.get("/api/auth/config").json()
        assert body == {"sso_enabled": True, "sso_provider": "microsoft"}

    disabled = _make_settings(tmp_path, enabled=False)
    with TestClient(_build_app(disabled, idp)) as client:
        body = client.get("/api/auth/config").json()
        assert body == {"sso_enabled": False, "sso_provider": None}


def test_routes_404_when_disabled(tmp_path) -> None:
    """With SSO off the login/callback routes do not exist."""
    idp = _MockIdP()
    settings = _make_settings(tmp_path, enabled=False)
    with TestClient(_build_app(settings, idp)) as client:
        assert client.get("/api/auth/oidc/login").status_code == 404
        assert client.get("/api/auth/oidc/callback").status_code == 404


# ----------------------------------------------------------------------
# Login redirect
# ----------------------------------------------------------------------


def test_login_redirects_to_authorize_with_pkce(tmp_path) -> None:
    """/login 302s to the MS authorize URL carrying PKCE + state + nonce."""
    idp = _MockIdP()
    settings = _make_settings(tmp_path)
    with TestClient(_build_app(settings, idp)) as client:
        resp = client.get(
            "/api/auth/oidc/login", follow_redirects=False
        )
        assert resp.status_code == 302
        loc = resp.headers["location"]
        assert loc.startswith(
            f"{_AUTHORITY}/{_TENANT}/oauth2/v2.0/authorize?"
        )
        assert "code_challenge=" in loc
        assert "code_challenge_method=S256" in loc
        assert f"client_id={_CLIENT_ID}" in loc
        assert "response_type=code" in loc
        assert resp.cookies.get("compass_oidc_state") is not None


def test_login_ignores_offsite_return(tmp_path) -> None:
    """A protocol-relative/offsite ?return is dropped (open-redirect guard)."""
    idp = _MockIdP()
    settings = _make_settings(tmp_path)
    with TestClient(_build_app(settings, idp)) as client:
        # Stash an offsite return and complete a happy-path callback; the
        # final redirect must NOT honour the offsite target.
        resp = client.get(
            "/api/auth/oidc/login?return=//evil.com/x",
            follow_redirects=False,
        )
        cookie = resp.cookies["compass_oidc_state"]
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(resp.headers["location"]).query)
        state, nonce = qs["state"][0], qs["nonce"][0]
        idp.id_token_for_code["code-1"] = idp.sign(
            idp.valid_claims(nonce=nonce)
        )
        cb = client.get(
            f"/api/auth/oidc/callback?code=code-1&state={state}",
            cookies={"compass_oidc_state": cookie},
            follow_redirects=False,
        )
        assert cb.status_code == 302
        # Falls back to "/" rather than the offsite location.
        assert cb.headers["location"] == "/"


# ----------------------------------------------------------------------
# Callback — happy paths
# ----------------------------------------------------------------------


def test_callback_autoprovisions_new_user(tmp_path) -> None:
    """First-time SSO sign-in provisions an SSO user + sets the auth cookie."""
    idp = _MockIdP()
    settings = _make_settings(tmp_path)
    app = _build_app(settings, idp)
    with TestClient(app) as client:
        state, nonce, cookies = _stage_login(client)
        idp.id_token_for_code["code-x"] = idp.sign(
            idp.valid_claims(
                email="new@gemeente.nl",
                oid="oid-new",
                tid="tid-new",
                nonce=nonce,
            )
        )
        resp = client.get(
            f"/api/auth/oidc/callback?code=code-x&state={state}",
            cookies=cookies,
            headers={"Host": "compass.example"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/chat"
        # Compass session cookie was set via the shared mechanism.
        assert resp.cookies.get(settings.auth_cookie_name) is not None

    # The user now exists with the SSO linkage + empty password.
    import asyncio

    async def _check() -> User | None:
        store = SQLiteStore(settings)
        await store.initialize()
        try:
            return await store.get_user_by_entra("oid-new", "tid-new")
        finally:
            await store.close()

    user = asyncio.get_event_loop().run_until_complete(_check())
    assert user is not None
    assert user.email == "new@gemeente.nl"
    assert user.hashed_password == ""
    assert user.auth_source == "sso"


def test_callback_links_existing_user_by_email(tmp_path) -> None:
    """An email-matching pre-existing account is linked, not duplicated."""
    idp = _MockIdP()
    settings = _make_settings(tmp_path)
    app = _build_app(settings, idp)

    import asyncio

    async def _seed() -> str:
        store = SQLiteStore(settings)
        await store.initialize()
        try:
            uid = generate_id()
            await store.create_user(
                User(
                    id=uid,
                    email="existing@gemeente.nl",
                    hashed_password="bcrypt-hash",
                )
            )
            return uid
        finally:
            await store.close()

    seeded_id = asyncio.get_event_loop().run_until_complete(_seed())

    with TestClient(app) as client:
        state, nonce, cookies = _stage_login(client)
        idp.id_token_for_code["code-link"] = idp.sign(
            idp.valid_claims(
                email="existing@gemeente.nl",
                oid="oid-link",
                tid="tid-link",
                nonce=nonce,
            )
        )
        resp = client.get(
            f"/api/auth/oidc/callback?code=code-link&state={state}",
            cookies=cookies,
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.cookies.get(settings.auth_cookie_name) is not None

    async def _verify() -> tuple[User | None, User | None]:
        store = SQLiteStore(settings)
        await store.initialize()
        try:
            by_entra = await store.get_user_by_entra("oid-link", "tid-link")
            by_email = await store.get_user_by_email("existing@gemeente.nl")
            return by_entra, by_email
        finally:
            await store.close()

    by_entra, by_email = asyncio.get_event_loop().run_until_complete(_verify())
    assert by_entra is not None
    # Same account (linked, not a new row): id unchanged, password preserved.
    assert by_entra.id == seeded_id
    assert by_email is not None and by_email.id == seeded_id
    assert by_entra.hashed_password == "bcrypt-hash"
    assert by_entra.auth_source == "sso"


# ----------------------------------------------------------------------
# Callback — rejection paths (all 302 -> /login?sso_error=1)
# ----------------------------------------------------------------------


def _assert_sso_error(resp) -> None:
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login?sso_error=1"


def test_callback_rejects_state_mismatch(tmp_path) -> None:
    """A ``state`` that doesn't match the signed cookie is rejected."""
    idp = _MockIdP()
    settings = _make_settings(tmp_path)
    with TestClient(_build_app(settings, idp)) as client:
        _state, nonce, cookies = _stage_login(client)
        idp.id_token_for_code["c"] = idp.sign(idp.valid_claims(nonce=nonce))
        resp = client.get(
            "/api/auth/oidc/callback?code=c&state=not-the-real-state",
            cookies=cookies,
            follow_redirects=False,
        )
        _assert_sso_error(resp)


def test_callback_rejects_tampered_state_cookie(tmp_path) -> None:
    """A signature-tampered state cookie fails the HMAC check."""
    idp = _MockIdP()
    settings = _make_settings(tmp_path)
    with TestClient(_build_app(settings, idp)) as client:
        state, _nonce, cookies = _stage_login(client)
        tampered = cookies["compass_oidc_state"][:-3] + "AAA"
        resp = client.get(
            f"/api/auth/oidc/callback?code=c&state={state}",
            cookies={"compass_oidc_state": tampered},
            follow_redirects=False,
        )
        _assert_sso_error(resp)


def test_callback_rejects_bad_signature(tmp_path) -> None:
    """An id_token signed by a foreign key fails JWKS validation."""
    idp = _MockIdP()
    settings = _make_settings(tmp_path)
    with TestClient(_build_app(settings, idp)) as client:
        state, nonce, cookies = _stage_login(client)
        idp.id_token_for_code["c"] = idp.sign(
            idp.valid_claims(nonce=nonce), key=idp.foreign_key()
        )
        resp = client.get(
            f"/api/auth/oidc/callback?code=c&state={state}",
            cookies=cookies,
            follow_redirects=False,
        )
        _assert_sso_error(resp)


def test_callback_rejects_aud_mismatch(tmp_path) -> None:
    """An id_token whose ``aud`` is not our client id is rejected."""
    idp = _MockIdP()
    settings = _make_settings(tmp_path)
    with TestClient(_build_app(settings, idp)) as client:
        state, nonce, cookies = _stage_login(client)
        idp.id_token_for_code["c"] = idp.sign(
            idp.valid_claims(nonce=nonce, aud="some-other-app")
        )
        resp = client.get(
            f"/api/auth/oidc/callback?code=c&state={state}",
            cookies=cookies,
            follow_redirects=False,
        )
        _assert_sso_error(resp)


def test_callback_rejects_expired_token(tmp_path) -> None:
    """An expired id_token (beyond leeway) is rejected."""
    idp = _MockIdP()
    settings = _make_settings(tmp_path)
    with TestClient(_build_app(settings, idp)) as client:
        state, nonce, cookies = _stage_login(client)
        idp.id_token_for_code["c"] = idp.sign(
            idp.valid_claims(nonce=nonce, exp_offset=-3600)
        )
        resp = client.get(
            f"/api/auth/oidc/callback?code=c&state={state}",
            cookies=cookies,
            follow_redirects=False,
        )
        _assert_sso_error(resp)


def test_callback_rejects_nonce_mismatch(tmp_path) -> None:
    """An id_token whose nonce differs from the flow's nonce is rejected."""
    idp = _MockIdP()
    settings = _make_settings(tmp_path)
    with TestClient(_build_app(settings, idp)) as client:
        state, _nonce, cookies = _stage_login(client)
        idp.id_token_for_code["c"] = idp.sign(
            idp.valid_claims(nonce="a-different-nonce")
        )
        resp = client.get(
            f"/api/auth/oidc/callback?code=c&state={state}",
            cookies=cookies,
            follow_redirects=False,
        )
        _assert_sso_error(resp)


def test_callback_rejects_missing_state_cookie(tmp_path) -> None:
    """No state cookie at all => generic SSO error."""
    idp = _MockIdP()
    settings = _make_settings(tmp_path)
    with TestClient(_build_app(settings, idp)) as client:
        resp = client.get(
            "/api/auth/oidc/callback?code=c&state=whatever",
            follow_redirects=False,
        )
        _assert_sso_error(resp)


# ----------------------------------------------------------------------
# Add-in mode (Office dialog: token delivered via messageParent)
# ----------------------------------------------------------------------


def _stage_addin_login(client: TestClient) -> tuple[str, str, dict[str, str]]:
    """Run /login?addin=1, returning ``(state, nonce, state-cookie dict)``."""
    resp = client.get(
        "/api/auth/oidc/login?addin=1&return=/chat",
        follow_redirects=False,
        headers={"Host": "compass.example"},
    )
    assert resp.status_code == 302
    from urllib.parse import parse_qs, urlparse

    qs = parse_qs(urlparse(resp.headers["location"]).query)
    cookie = resp.cookies.get("compass_oidc_state")
    assert cookie is not None
    return qs["state"][0], qs["nonce"][0], {"compass_oidc_state": cookie}


def test_login_addin_flag_is_carried_in_signed_state(tmp_path) -> None:
    """``?addin=1`` records ``addin: true`` inside the signed state cookie."""
    idp = _MockIdP()
    settings = _make_settings(tmp_path)
    with TestClient(_build_app(settings, idp)) as client:
        _state, _nonce, cookies = _stage_addin_login(client)
        # Decode the signed cookie body (URL-safe-base64 JSON) and assert the
        # flag landed inside it — the callback trusts this, not a query param.
        import base64

        body = cookies["compass_oidc_state"].split(".", 1)[0]
        padding = "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body + padding))
        assert payload["addin"] is True

    # A login WITHOUT the flag records addin: false.
    with TestClient(_build_app(settings, idp)) as client:
        _state, _nonce, cookies = _stage_login(client)
        import base64

        body = cookies["compass_oidc_state"].split(".", 1)[0]
        padding = "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body + padding))
        assert payload["addin"] is False


def test_callback_addin_redirects_to_completion_with_token(tmp_path) -> None:
    """
    Add-in callback success → 302 to the same-origin completion page with the
    minted token in the URL fragment.

    The callback runs on compass.example but the task pane lives on
    addins.example, so it cannot ``messageParent`` itself (cross-origin =
    silent no-op = hung dialog). It must bounce to the add-in-origin
    completion page; the token rides in the fragment (never sent to a server)
    and that page does the messageParent.
    """
    from urllib.parse import parse_qs, urlparse

    idp = _MockIdP()
    settings = _make_settings(tmp_path)
    with TestClient(_build_app(settings, idp)) as client:
        state, nonce, cookies = _stage_addin_login(client)
        idp.id_token_for_code["code-addin"] = idp.sign(
            idp.valid_claims(
                email="addin@gemeente.nl",
                oid="oid-addin",
                tid="tid-addin",
                nonce=nonce,
            )
        )
        resp = client.get(
            f"/api/auth/oidc/callback?code=code-addin&state={state}",
            cookies=cookies,
            headers={"Host": "compass.example"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers["location"]
        parsed = urlparse(location)
        # Bounces to the configured same-origin completion page (no query —
        # the result lives in the fragment so the token stays out of logs).
        base, _, fragment = location.partition("#")
        assert base == settings.oidc_addin_completion_url
        assert parsed.query == ""
        frag = parse_qs(fragment)
        assert "error" not in frag
        # No web session cookie is set in the add-in flow.
        assert resp.cookies.get(settings.auth_cookie_name) is None
        # The token in the fragment validates as our session token.
        token = frag["access_token"][0]
        authenticator = JWTAuthenticator(settings)
        validated = authenticator.validate_token(token)
        assert validated.email == "addin@gemeente.nl"


def test_callback_addin_failure_redirects_to_completion_with_error(
    tmp_path,
) -> None:
    """
    Add-in callback failure → 302 to the completion page with a generic error
    in the fragment (no token, no reason/stack leak).
    """
    from urllib.parse import parse_qs

    idp = _MockIdP()
    settings = _make_settings(tmp_path)
    with TestClient(_build_app(settings, idp)) as client:
        state, nonce, cookies = _stage_addin_login(client)
        # Foreign-key signature → id_token validation fails AFTER the state
        # cookie (carrying addin) has been verified, so the add-in failure
        # surface (completion redirect) must be used, not the web redirect.
        idp.id_token_for_code["code-bad"] = idp.sign(
            idp.valid_claims(nonce=nonce), key=idp.foreign_key()
        )
        resp = client.get(
            f"/api/auth/oidc/callback?code=code-bad&state={state}",
            cookies=cookies,
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers["location"]
        base, _, fragment = location.partition("#")
        assert base == settings.oidc_addin_completion_url
        frag = parse_qs(fragment)
        assert frag.get("error") == ["sso_failed"]
        # No token, no web session cookie.
        assert "access_token" not in frag
        assert resp.cookies.get(settings.auth_cookie_name) is None


def test_callback_non_addin_still_sets_cookie_and_redirects(tmp_path) -> None:
    """The default (non-add-in) callback is unchanged: 302 + session cookie."""
    idp = _MockIdP()
    settings = _make_settings(tmp_path)
    with TestClient(_build_app(settings, idp)) as client:
        state, nonce, cookies = _stage_login(client)
        idp.id_token_for_code["code-web"] = idp.sign(
            idp.valid_claims(nonce=nonce)
        )
        resp = client.get(
            f"/api/auth/oidc/callback?code=code-web&state={state}",
            cookies=cookies,
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/chat"
        assert resp.cookies.get(settings.auth_cookie_name) is not None
