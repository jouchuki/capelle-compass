"""
Entra/Azure AD OIDC SSO handler (compass-entra-oidc-sso).

Implements the authorization-code + PKCE flow with the backend as the
confidential client. Two routes hang off ``/api/auth/oidc``:

* ``GET /login`` — generate ``state`` + PKCE ``code_verifier`` + ``nonce``,
  stash them in a short-TTL HMAC-signed cookie, and 302 the browser to the
  Microsoft authorize endpoint.
* ``GET /callback`` — verify the signed state cookie, exchange the code at
  the tenant token endpoint, validate the returned ``id_token`` against the
  tenant JWKS (signature + iss + aud + exp + nonce), then link the Entra
  identity to a Compass account (by oid/tid, else by email, else
  auto-provision) and mint the *same* Compass session cookie that password
  login issues. On any failure it 302s to ``/login?sso_error=1`` — never
  leaking a stack trace or the upstream error to the user.

Design boundaries
-----------------
* No new session system: the handler reuses
  :meth:`JWTAuthenticator.create_token` and the exact cookie-writing
  mechanism :meth:`AuthHandler._set_auth_cookie` uses — SSO is just an
  alternate way to obtain the existing session.
* All network IO (discovery, JWKS, token exchange) is delegated to
  :class:`OidcDiscoveryClient`, which is injectable so tests can drive a
  mock IdP without monkeypatching module internals.
* The flow is config-gated: when ``settings.oidc_enabled`` is False the
  handler is constructed but never registered (the Builder skips it) — and,
  defensively, every route also returns 404 when disabled.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Final
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from capelle_platform.auth.base_authenticator import BaseAuthenticator
from capelle_platform.models.user import TokenResponse, User
from capelle_platform.observability import get_logger
from capelle_platform.observability.logger import TraceContext
from capelle_platform.settings import Settings
from capelle_platform.store.base_store import BaseStore
from capelle_platform.utils import generate_id
from capelle_platform.web.host import domain_for_host

_logger = get_logger(__name__)

#: Lifetime of the signed state cookie. The user must complete the IdP
#: round-trip within this window; ten minutes is generous for an
#: interactive login while keeping a stale/replayed cookie short-lived.
_STATE_TTL_SECONDS: Final[int] = 600

#: Name of the short-TTL signed cookie carrying state/PKCE/nonce. Host-only
#: (no Domain attribute) so it never leaks to a sibling subdomain.
_STATE_COOKIE_NAME: Final[str] = "compass_oidc_state"

#: Microsoft identity-platform authorize/token base. ``<tenant>`` is
#: interpolated from settings (``organizations`` for multi-tenant).
_MS_AUTHORITY_BASE: Final[str] = "https://login.microsoftonline.com"

#: Where to send the browser after an SSO failure. A query flag lets the
#: login screen render a friendly message; no error detail is exposed.
_SSO_ERROR_REDIRECT: Final[str] = "/login?sso_error=1"

#: Default post-login landing path when no (valid) ``return`` was supplied.
_DEFAULT_RETURN_PATH: Final[str] = "/"

#: Clock-skew tolerance (seconds) allowed when validating ``exp``/``iat``.
_LEEWAY_SECONDS: Final[int] = 60

#: Truthy query-flag values for ``?addin=...``. Anything else (absent,
#: empty, ``0``, ``false``) is treated as the ordinary web flow.
_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


def _is_truthy(raw: str | None) -> bool:
    """True when a query flag carries an affirmative value."""
    return raw is not None and raw.strip().lower() in _TRUTHY


def _b64url_encode(raw: bytes) -> str:
    """URL-safe base64 without padding (PKCE + cookie-signature encoding)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    """Inverse of :func:`_b64url_encode`, restoring the stripped padding."""
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _safe_return_path(raw: str | None) -> str:
    """
    Sanitise a caller-supplied ``return`` into a same-site path.

    Only a path beginning with a single ``/`` (and not ``//`` — which a
    browser treats as a protocol-relative absolute URL) is honoured;
    everything else falls back to :data:`_DEFAULT_RETURN_PATH`. This blocks
    an open-redirect: the SSO callback must never bounce a user to an
    attacker-controlled origin.
    """
    if not raw:
        return _DEFAULT_RETURN_PATH
    if not raw.startswith("/") or raw.startswith("//"):
        return _DEFAULT_RETURN_PATH
    return raw


class OidcDiscoveryClient:
    """
    Fetches + caches OIDC discovery and JWKS, and runs the token exchange.

    One instance per process. Discovery and JWKS are cached per tenant for
    the lifetime of the process (the document is effectively static and key
    rollover is rare; a restart re-fetches). All HTTP goes through an
    injected :class:`httpx.AsyncClient` so tests can mount a mock transport
    serving a local IdP.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        authority_base: str = _MS_AUTHORITY_BASE,
    ) -> None:
        """
        Args:
            client: Async HTTP client used for every outbound call. The
                caller owns its lifecycle (the Builder closes it on
                shutdown); tests inject one backed by a mock transport.
            authority_base: Identity-platform base URL. Overridable so a
                test's mock IdP can be addressed.
        """
        self._client = client
        self._authority_base = authority_base.rstrip("/")
        self._discovery_cache: dict[str, dict[str, Any]] = {}
        self._jwks_cache: dict[str, jwt.PyJWKSet] = {}

    def _discovery_url(self, tenant: str) -> str:
        """Well-known discovery URL for ``tenant``."""
        return (
            f"{self._authority_base}/{tenant}/v2.0/"
            ".well-known/openid-configuration"
        )

    async def get_discovery(self, tenant: str) -> dict[str, Any]:
        """Return the (cached) discovery document for ``tenant``."""
        cached = self._discovery_cache.get(tenant)
        if cached is not None:
            return cached
        resp = await self._client.get(self._discovery_url(tenant))
        resp.raise_for_status()
        doc: dict[str, Any] = resp.json()
        self._discovery_cache[tenant] = doc
        return doc

    async def get_jwks(self, tenant: str) -> jwt.PyJWKSet:
        """Return the (cached) JWK set for ``tenant``."""
        cached = self._jwks_cache.get(tenant)
        if cached is not None:
            return cached
        doc = await self.get_discovery(tenant)
        jwks_uri = doc["jwks_uri"]
        resp = await self._client.get(jwks_uri)
        resp.raise_for_status()
        jwks = jwt.PyJWKSet.from_dict(resp.json())
        self._jwks_cache[tenant] = jwks
        return jwks

    async def exchange_code(
        self,
        tenant: str,
        *,
        code: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str,
        code_verifier: str,
    ) -> dict[str, Any]:
        """
        Exchange an authorization ``code`` for tokens at the token endpoint.

        Sends the confidential-client credentials plus the PKCE
        ``code_verifier``. Returns the parsed token response (carrying the
        ``id_token``). Raises for a non-2xx upstream status so the callback
        funnels it into the generic SSO-error redirect.
        """
        doc = await self.get_discovery(tenant)
        token_endpoint: str = doc["token_endpoint"]
        form = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
        resp = await self._client.post(token_endpoint, data=form)
        resp.raise_for_status()
        return resp.json()

    async def signing_key_for(self, tenant: str, token: str) -> jwt.PyJWK:
        """
        Resolve the JWK that signed ``token`` from the tenant JWK set.

        Reads the token's ``kid`` header and looks it up in the cached
        JWKS. Raises ``jwt.PyJWKClientError`` when the key is unknown.
        """
        jwks = await self.get_jwks(tenant)
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        for key in jwks.keys:
            if key.key_id == kid:
                return key
        raise jwt.PyJWKClientError(f"No JWK matches kid {kid!r}")


class OidcHandler:
    """
    HTTP handler for the Entra OIDC SSO login + callback.

    Depends only on interfaces (:class:`BaseAuthenticator`,
    :class:`BaseStore`) plus the injectable :class:`OidcDiscoveryClient`
    so the network edge is fully mockable. Cookie minting is delegated to
    the supplied ``set_auth_cookie`` callable — the very method
    :class:`AuthHandler` uses — so the SSO session is byte-for-byte the
    password-login session.
    """

    def __init__(
        self,
        authenticator: BaseAuthenticator,
        store: BaseStore,
        settings: Settings,
        discovery: OidcDiscoveryClient,
        set_auth_cookie: Any,
    ) -> None:
        """
        Args:
            authenticator: Mints the Compass JWT (``create_token``).
            store: User persistence (lookup/link/provision).
            settings: OIDC config + the JWT secret used to sign the state
                cookie + the cookie security flags.
            discovery: OIDC discovery/JWKS/token-exchange client.
            set_auth_cookie: Callable ``(Response, TokenResponse) -> None``
                that writes the Compass session cookie. Injected from
                :meth:`AuthHandler._set_auth_cookie` so SSO and password
                login share one cookie mechanism.
        """
        self._auth = authenticator
        self._store = store
        self._settings = settings
        self._discovery = discovery
        self._set_auth_cookie = set_auth_cookie
        self.router = APIRouter(prefix="/api/auth/oidc", tags=["auth"])
        self.router.add_api_route(
            "/login", self.login, methods=["GET"], include_in_schema=False
        )
        self.router.add_api_route(
            "/callback",
            self.callback,
            methods=["GET"],
            include_in_schema=False,
        )

    # ------------------------------------------------------------------
    # Signed state cookie (HMAC over the JWT secret)
    # ------------------------------------------------------------------

    def _sign_state(self, payload: dict[str, Any]) -> str:
        """
        Serialise ``payload`` to a tamper-evident ``<body>.<sig>`` token.

        The body is URL-safe-base64 JSON; the signature is
        HMAC-SHA256(body) keyed by the app JWT secret. We sign rather than
        encrypt because the contents (state/nonce/verifier/return) are not
        secret — only their integrity matters (an attacker must not be able
        to forge a matching ``state``). Carrying it in a cookie keeps the
        flow stateless so it works across the multi-yuta pool.
        """
        body = _b64url_encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )
        sig = self._compute_sig(body)
        return f"{body}.{sig}"

    def _compute_sig(self, body: str) -> str:
        """HMAC-SHA256 of ``body`` keyed by the app JWT secret."""
        digest = hmac.new(
            self._settings.jwt_secret.encode("utf-8"),
            body.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return _b64url_encode(digest)

    def _verify_state(self, token: str) -> dict[str, Any]:
        """
        Verify + decode a signed state cookie, enforcing its TTL.

        Raises :class:`ValueError` on a malformed token, a signature
        mismatch (constant-time compared), or an expired ``ts`` — the
        callback maps any failure to the generic SSO-error redirect.
        """
        try:
            body, sig = token.split(".", 1)
        except ValueError as exc:
            raise ValueError("malformed state cookie") from exc
        expected = self._compute_sig(body)
        if not hmac.compare_digest(sig, expected):
            raise ValueError("state signature mismatch")
        try:
            payload: dict[str, Any] = json.loads(_b64url_decode(body))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("undecodable state cookie") from exc
        issued = float(payload.get("ts", 0))
        if time.time() - issued > _STATE_TTL_SECONDS:
            raise ValueError("state cookie expired")
        return payload

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    async def login(self, request: Request) -> Response:
        """
        Start the OIDC flow: stash state/PKCE/nonce, 302 to authorize.

        Honors ``?return=<path>`` (same-site paths only). When ``?addin=1``
        (any truthy value) is supplied the flow's origin is recorded INSIDE
        the signed state cookie so the callback can deliver the token to the
        Office add-in dialog instead of setting the web session cookie — the
        flag is never trusted from a callback query param. Returns 404 when
        SSO is disabled so the route is invisible without an Azure app.
        """
        if not self._settings.oidc_enabled:
            raise HTTPException(status_code=404, detail="Not found")

        trace_id = TraceContext.new()
        addin = _is_truthy(request.query_params.get("addin"))
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = _b64url_encode(
            hashlib.sha256(code_verifier.encode("ascii")).digest()
        )
        return_path = _safe_return_path(request.query_params.get("return"))

        state_payload = {
            "state": state,
            "code_verifier": code_verifier,
            "nonce": nonce,
            "return_path": return_path,
            # Flow origin: carried inside the signed cookie so the callback
            # trusts it (HMAC-protected), not a forgeable callback query param.
            "addin": addin,
            "ts": time.time(),
        }
        signed = self._sign_state(state_payload)

        authorize_url = (
            f"{_MS_AUTHORITY_BASE}/{self._settings.oidc_tenant}"
            "/oauth2/v2.0/authorize?"
            + urlencode(
                {
                    "client_id": self._settings.oidc_client_id,
                    "response_type": "code",
                    "redirect_uri": self._settings.oidc_redirect_uri,
                    "response_mode": "query",
                    "scope": self._settings.oidc_scopes,
                    "state": state,
                    "nonce": nonce,
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                }
            )
        )

        _logger.info("oidc_login_started", trace_id=trace_id)
        response = RedirectResponse(url=authorize_url, status_code=302)
        # Host-only, HttpOnly, short-TTL signed cookie. SameSite=lax so the
        # IdP's top-level GET redirect back to /callback still carries it.
        response.set_cookie(
            key=_STATE_COOKIE_NAME,
            value=signed,
            max_age=_STATE_TTL_SECONDS,
            httponly=True,
            secure=self._settings.auth_cookie_secure,
            samesite="lax",
            path="/",
        )
        return response

    async def callback(self, request: Request, response: Response) -> Response:
        """
        Complete the OIDC flow: validate, link/provision, mint the session.

        Two flow origins, distinguished by the ``addin`` flag carried in the
        signed state cookie:

        * **Web (default)** — on success the Compass session cookie is set and
          the browser is 302'd to the stored return path; on failure it is
          302'd to :data:`_SSO_ERROR_REDIRECT` with no error detail.
        * **Add-in** — the Office dialog cannot use the cross-origin HttpOnly
          cookie, so on success a self-contained HTML page (200 text/html)
          hands the minted Bearer token back via
          ``Office.context.ui.messageParent``; on failure the same page shape
          posts ``{"error": ...}`` with no stack/upstream detail.

        Whether the flow is an add-in flow is only knowable AFTER the state
        cookie is verified, so a failure that occurs before verification (no
        cookie at all) cannot be an add-in flow and falls back to the web
        error redirect. Returns 404 when SSO is disabled.
        """
        if not self._settings.oidc_enabled:
            raise HTTPException(status_code=404, detail="Not found")

        trace_id = TraceContext.new()
        try:
            user, return_path, addin = await self._complete_callback(
                request, trace_id
            )
        except _CallbackError as exc:
            _logger.info(
                "oidc_callback_rejected", reason=exc.reason, trace_id=trace_id
            )
            return self._failure_response(exc.addin)
        except Exception:  # noqa: BLE001 — never leak an upstream failure
            _logger.exception("oidc_callback_failed", trace_id=trace_id)
            return self._error_redirect()

        token: TokenResponse = self._auth.create_token(user)

        if addin:
            _logger.info(
                "oidc_login_succeeded_addin",
                user_id=user.id,
                trace_id=trace_id,
            )
            return self._addin_success_response(token)

        redirect = RedirectResponse(url=return_path, status_code=302)
        # Reuse the password-login cookie mechanism verbatim.
        self._set_auth_cookie(redirect, token)
        # Clear the now-spent state cookie.
        redirect.delete_cookie(
            key=_STATE_COOKIE_NAME,
            path="/",
            secure=self._settings.auth_cookie_secure,
            samesite="lax",
        )
        _logger.info(
            "oidc_login_succeeded", user_id=user.id, trace_id=trace_id
        )
        return redirect

    def _error_redirect(self) -> RedirectResponse:
        """302 to the login screen's SSO-error state, clearing state cookie."""
        redirect = RedirectResponse(url=_SSO_ERROR_REDIRECT, status_code=302)
        redirect.delete_cookie(
            key=_STATE_COOKIE_NAME,
            path="/",
            secure=self._settings.auth_cookie_secure,
            samesite="lax",
        )
        return redirect

    def _failure_response(self, addin: bool) -> Response:
        """
        Dispatch a rejected callback to the right failure surface.

        Add-in flows get an HTML page that ``messageParent``s a generic
        ``{"error": ...}`` (no reason/stack detail); web flows get the
        existing SSO-error redirect.
        """
        if addin:
            return self._addin_error_response()
        return self._error_redirect()

    def _addin_completion_redirect(
        self, result: dict[str, str]
    ) -> RedirectResponse:
        """
        302 the add-in dialog to the same-origin completion page.

        The callback runs on ``oidc_redirect_uri`` (app.example.com), a
        *different* domain than the add-in task pane (addins.example.com).
        ``Office.context.ui.messageParent`` only reaches the opener when the
        calling page is same-origin with the task pane, so a messageParent
        emitted here would be a silent no-op and the dialog would hang.
        Instead we bounce to :attr:`Settings.oidc_addin_completion_url` — a
        page served from the add-in origin — and carry ``result`` in the URL
        **fragment** (``#access_token=...`` on success, ``#error=...`` on
        failure). The fragment is never sent to the completion page's server,
        so the token stays out of access logs; only that page's same-origin
        JS reads it and performs the messageParent. The spent state cookie is
        cleared so a replay can't reuse it.
        """
        target = (
            f"{self._settings.oidc_addin_completion_url}#{urlencode(result)}"
        )
        response = RedirectResponse(url=target, status_code=302)
        response.delete_cookie(
            key=_STATE_COOKIE_NAME,
            path="/",
            secure=self._settings.auth_cookie_secure,
            samesite="lax",
        )
        return response

    def _addin_success_response(
        self, token: TokenResponse
    ) -> RedirectResponse:
        """302 the add-in dialog to the completion page with the minted token."""
        return self._addin_completion_redirect(
            {"access_token": token.access_token}
        )

    def _addin_error_response(self) -> RedirectResponse:
        """
        302 the add-in dialog to the completion page with a generic error.

        Mirrors :data:`_SSO_ERROR_REDIRECT` for the add-in surface: the dialog
        learns the flow failed without any reason/stack leaking.
        """
        return self._addin_completion_redirect({"error": "sso_failed"})

    async def _complete_callback(
        self, request: Request, trace_id: str
    ) -> tuple[User, str, bool]:
        """
        Core callback logic, raising :class:`_CallbackError` on any reject.

        Returns ``(user, return_path, addin)`` on success — ``addin`` is the
        flow-origin flag read back from the signed state cookie (never from a
        callback query param). Split out from :meth:`callback` so the route
        stays a thin try/except funnel.
        """
        params = request.query_params
        # Microsoft signals user-side failures via error params, not a code.
        if params.get("error"):
            raise _CallbackError("idp_error")
        code = params.get("code")
        state = params.get("state")
        if not code or not state:
            raise _CallbackError("missing_code_or_state")

        raw_cookie = request.cookies.get(_STATE_COOKIE_NAME)
        if not raw_cookie:
            raise _CallbackError("missing_state_cookie")
        try:
            stashed = self._verify_state(raw_cookie)
        except ValueError as exc:
            raise _CallbackError(f"bad_state_cookie:{exc}") from exc
        if not hmac.compare_digest(str(stashed.get("state", "")), state):
            raise _CallbackError("state_mismatch")

        # Flow origin is now trustworthy (signed cookie verified). Every
        # rejection from here on must surface on the matching failure surface,
        # so re-tag any untagged _CallbackError with this flag.
        addin = bool(stashed.get("addin", False))
        try:
            user = await self._exchange_and_resolve(
                request, stashed, code=code
            )
        except _CallbackError as exc:
            if not exc.addin:
                exc.addin = addin
            raise
        return user, _safe_return_path(str(stashed.get("return_path", "/"))), addin

    async def _exchange_and_resolve(
        self, request: Request, stashed: dict[str, Any], *, code: str
    ) -> User:
        """
        Token exchange + id_token validation + user link/provision.

        Split from :meth:`_complete_callback` so the post-verification steps
        — every one of which can reject — sit under a single re-tagging
        ``except`` that stamps the flow-origin flag onto the error.
        """
        tenant = self._settings.oidc_tenant
        try:
            tokens = await self._discovery.exchange_code(
                tenant,
                code=code,
                redirect_uri=self._settings.oidc_redirect_uri,
                client_id=self._settings.oidc_client_id,
                client_secret=self._settings.oidc_client_secret,
                code_verifier=str(stashed["code_verifier"]),
            )
        except httpx.HTTPError as exc:
            raise _CallbackError("token_exchange_failed") from exc

        id_token = tokens.get("id_token")
        if not id_token:
            raise _CallbackError("no_id_token")

        claims = await self._validate_id_token(
            tenant, id_token, expected_nonce=str(stashed["nonce"])
        )

        email = self._extract_email(claims)
        oid = claims.get("oid")
        tid = claims.get("tid")
        if not oid or not tid:
            raise _CallbackError("missing_oid_or_tid")

        host = request.headers.get("host", "")
        domain = domain_for_host(host)
        return await self._resolve_user(
            email=email, oid=str(oid), tid=str(tid), domain=domain
        )

    async def _validate_id_token(
        self, tenant: str, id_token: str, *, expected_nonce: str
    ) -> dict[str, Any]:
        """
        Validate the id_token signature + standard claims + nonce.

        Verifies the RS256 signature against the tenant JWKS, enforces
        ``aud == client_id`` and ``exp`` (with leeway), then checks the
        ``nonce`` matches the value bound to this flow — defeating token
        replay. ``iss`` is validated structurally (must be a
        login.microsoftonline.com issuer) rather than pinned to a single
        tenant, since multi-tenant ``organizations`` yields a
        per-home-tenant issuer.
        """
        try:
            signing_key = await self._discovery.signing_key_for(
                tenant, id_token
            )
            claims: dict[str, Any] = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._settings.oidc_client_id,
                leeway=_LEEWAY_SECONDS,
                options={"require": ["exp", "aud", "iss"]},
            )
        except jwt.InvalidTokenError as exc:
            raise _CallbackError(f"id_token_invalid:{exc}") from exc
        except jwt.PyJWKClientError as exc:
            raise _CallbackError("id_token_no_key") from exc

        issuer = str(claims.get("iss", ""))
        if not issuer.startswith(f"{_MS_AUTHORITY_BASE}/"):
            raise _CallbackError("issuer_untrusted")

        nonce = str(claims.get("nonce", ""))
        if not hmac.compare_digest(nonce, expected_nonce):
            raise _CallbackError("nonce_mismatch")
        return claims

    @staticmethod
    def _extract_email(claims: dict[str, Any]) -> str:
        """
        Pull a usable email from the validated id_token claims.

        Entra surfaces the address under ``email``, ``preferred_username``,
        or ``upn`` depending on tenant config; we try them in that order.
        Raises :class:`_CallbackError` when none is present (we cannot link
        or provision without an email).
        """
        for key in ("email", "preferred_username", "upn"):
            value = claims.get(key)
            if isinstance(value, str) and "@" in value:
                return value.strip().lower()
        raise _CallbackError("no_email_claim")

    async def _resolve_user(
        self, *, email: str, oid: str, tid: str, domain: str
    ) -> User:
        """
        Resolve the Compass account for a validated Entra identity.

        Resolution order: (1) durable ``(oid, tid)`` lookup; (2) link by
        email to a pre-existing account, persisting the oid/tid key; (3)
        auto-provision a fresh SSO account (empty ``hashed_password``,
        ``auth_source='sso'``, domain tagged from the request host). Always
        returns a usable :class:`User`.
        """
        by_entra = await self._store.get_user_by_entra(oid, tid)
        if by_entra is not None:
            return by_entra

        by_email = await self._store.get_user_by_email(email)
        if by_email is not None:
            await self._store.link_entra_identity(by_email.id, oid, tid)
            # Reflect the linkage on the returned model so the freshly
            # minted token + any downstream read see the SSO source.
            return by_email.model_copy(
                update={
                    "entra_oid": oid,
                    "entra_tid": tid,
                    "auth_source": "sso",
                    # The IdP vouches for the address — SSO accounts are
                    # always treated as verified, exempt from the email gate.
                    "email_verified": True,
                }
            )

        user = User(
            id=generate_id(),
            email=email,
            hashed_password="",
            entra_oid=oid,
            entra_tid=tid,
            auth_source="sso",
            # SSO identities are pre-verified by the provider; never gated.
            email_verified=True,
        )
        await self._store.create_user(user)
        _logger.info(
            "oidc_user_provisioned", user_id=user.id, domain=domain
        )
        return user


class _CallbackError(Exception):
    """
    Internal sentinel for a rejected OIDC callback.

    Carries a short machine reason for structured logging; the user-facing
    response carries no detail (a generic SSO-error redirect or, for add-in
    flows, a generic ``{"error": ...}`` page), so the reason never reaches
    the browser. ``addin`` records whether the rejected flow is an add-in
    flow — known only once the signed state cookie has been verified, so it
    defaults to ``False`` for failures raised before verification.
    """

    def __init__(self, reason: str, *, addin: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.addin = addin
