"""
Authentication handler.

Exposes register, login, and logout endpoints.  Decision logic only —
actual password hashing and token issuance is delegated to the
BaseAuthenticator implementation. Auth tokens are issued as HttpOnly
cookies so XSS-stolen ``localStorage`` cannot exfiltrate them; the
legacy ``TokenResponse`` body still carries the token for non-browser
clients and tests.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr

from capelle_platform.auth.base_authenticator import BaseAuthenticator
from capelle_platform.auth.email_verification import EmailVerificationService
from capelle_platform.auth.revocation import BaseTokenRevocationRegistry
from capelle_platform.email.base_sender import BaseEmailSender
from capelle_platform.models.user import TokenResponse, User, UserCreate
from capelle_platform.observability import get_logger
from capelle_platform.observability.logger import TraceContext
from capelle_platform.settings import Settings
from capelle_platform.store.base_store import BaseStore
from capelle_platform.utils import generate_id
from capelle_platform.utils.client_ip import ClientIpResolver
from capelle_platform.utils.rate_limit import SlidingWindowRateLimiter

_logger = get_logger(__name__)

_AUTH_WINDOW_SECONDS = 300  # 5 minutes
_AUTH_MAX_PER_EMAIL = 5
_AUTH_MAX_PER_IP = 20

#: Detail code returned (HTTP 403) when an unverified password account tries
#: to log in. The frontend keys on this to show the "resend verification"
#: affordance rather than a generic credentials error.
EMAIL_NOT_VERIFIED_CODE = "email_not_verified"


class ResendVerificationRequest(BaseModel):
    """Payload for re-requesting a verification email."""

    email: EmailStr


class AuthHandler:
    """
    HTTP handler for user registration and authentication.

    Depends on BaseAuthenticator (password + token ops) and BaseStore
    (user persistence).  No direct IO — all delegated through interfaces.
    """

    def __init__(
        self,
        authenticator: BaseAuthenticator,
        store: BaseStore,
        settings: Settings,
        revocation: BaseTokenRevocationRegistry | None = None,
        email_sender: BaseEmailSender | None = None,
        verification_service: EmailVerificationService | None = None,
    ) -> None:
        """
        Construct with injected dependencies.

        Args:
            authenticator: Handles password hashing and JWT issuance.
            store: Persistence layer for user records.
            settings: Application settings (cookie name, cookie security flags).
            revocation: Per-user token revocation registry (API-3). When
                supplied, ``logout`` records a revocation instant so live
                WebSockets for this user are torn down before the token's
                natural expiry.
            email_sender: Transport for the verification email. Required
                (together with ``verification_service`` and the settings
                switch) for the email-verification gate to activate.
            verification_service: Mints/validates the link tokens.
        """
        self._auth = authenticator
        self._store = store
        self._settings = settings
        self._revocation = revocation
        self._email_sender = email_sender
        self._verification_service = verification_service
        # The gate is active only when explicitly enabled AND both
        # collaborators are wired. Absent either (legacy wiring, the test
        # suite, the Capelle deployment), registration/login keep their
        # original auto-login behaviour — nothing changes.
        self._verification_active = bool(
            settings.email_verification_enabled
            and email_sender is not None
            and verification_service is not None
        )
        self._email_limiter = SlidingWindowRateLimiter(
            max_hits=_AUTH_MAX_PER_EMAIL,
            window_seconds=_AUTH_WINDOW_SECONDS,
        )
        self._ip_limiter = SlidingWindowRateLimiter(
            max_hits=_AUTH_MAX_PER_IP,
            window_seconds=_AUTH_WINDOW_SECONDS,
        )
        self._ip_resolver = ClientIpResolver(settings.trusted_proxy_set)
        # API-6: the limiter is per-process. In a multi-instance topology
        # (queue/ws_hub on rabbitmq → multiple yutas behind the LB) the caps
        # multiply by the fleet size and counters are wiped on restart. There
        # is no shared-store limiter yet, so warn loudly at construction so an
        # operator notices the gap; a Redis/store-backed limiter mirroring the
        # Builder's impl-selection is the durable fix.
        if settings.queue == "rabbitmq" or settings.ws_hub == "rabbitmq":
            _logger.warning(
                "auth_rate_limiter_in_memory_multi_instance",
                queue=settings.queue,
                ws_hub=settings.ws_hub,
                detail=(
                    "Auth rate limiting is per-process but this deployment is "
                    "multi-instance; effective caps multiply by the number of "
                    "yutas and reset on restart. Move limiting to the LB or "
                    "back it with a shared store."
                ),
            )
        self.router = APIRouter(prefix="/api/auth", tags=["auth"])
        self.router.add_api_route("/register", self.register, methods=["POST"])
        self.router.add_api_route("/login", self.login, methods=["POST"])
        self.router.add_api_route("/logout", self.logout, methods=["POST"])
        self.router.add_api_route("/me", self.me, methods=["GET"])
        self.router.add_api_route("/config", self.config, methods=["GET"])
        self.router.add_api_route("/verify", self.verify, methods=["GET"])
        self.router.add_api_route(
            "/resend-verification", self.resend_verification, methods=["POST"]
        )

    def _rate_limit(self, request: Request, email: str) -> None:
        """
        Enforce per-email and per-IP auth attempt caps.

        Raises 429 with ``Retry-After`` when either bucket is exhausted.
        Email is normalised to lowercase so case variants share a bucket.
        """
        ip = self._ip_resolver.resolve(request)
        ip_ok, ip_retry = self._ip_limiter.check(f"ip:{ip}")
        if not ip_ok:
            _logger.info("auth_rate_limited", scope="ip", ip=ip, retry_after=ip_retry)
            raise HTTPException(
                status_code=429,
                detail="Te veel pogingen. Probeer het later opnieuw.",
                headers={"Retry-After": str(ip_retry)},
            )
        email_ok, email_retry = self._email_limiter.check(
            f"email:{email.strip().lower()}"
        )
        if not email_ok:
            _logger.info(
                "auth_rate_limited",
                scope="email",
                email=email,
                retry_after=email_retry,
            )
            raise HTTPException(
                status_code=429,
                detail="Te veel pogingen voor dit account. Probeer het later opnieuw.",
                headers={"Retry-After": str(email_retry)},
            )

    def _set_auth_cookie(self, response: Response, token: TokenResponse) -> None:
        """
        Write the JWT onto an HttpOnly cookie on ``response``.

        Cookie lifetime mirrors the JWT expiry so the browser stops
        replaying it once the token is invalid anyway. ``Secure`` is
        toggled via settings so local dev over http still works.
        """
        response.set_cookie(
            key=self._settings.auth_cookie_name,
            value=token.access_token,
            max_age=self._settings.jwt_expiry_minutes * 60,
            httponly=True,
            secure=self._settings.auth_cookie_secure,
            samesite="lax",
            path="/",
        )

    def _gate_active(self, request: Request) -> bool:
        """
        Whether the email-verification gate applies to THIS request.

        """
        return self._verification_active

    def _request_origin(self, request: Request) -> str:
        """
        Return the configured public origin for account links.

        """
        return self._settings.public_origin.rstrip("/")

    async def _send_verification_email(
        self, user: User, request: Request, trace_id: str
    ) -> None:
        """
        Mint a verification token and email the link. Best-effort: a transport
        failure is logged but never raised, so a flaky provider can't 500 a
        registration — the user can re-request via ``/resend-verification``.

        Only called when :attr:`_verification_active`, so the collaborators are
        guaranteed present.
        """
        assert self._verification_service is not None  # _verification_active
        assert self._email_sender is not None
        token = self._verification_service.issue(user.id)
        origin = self._request_origin(request)
        link = f"{origin}/api/auth/verify?token={token}"
        subject = "Bevestig je e-mailadres voor Compass"
        html = (
            "<p>Welkom bij Compass.</p>"
            "<p>Bevestig je e-mailadres om je account te activeren:</p>"
            f'<p><a href="{link}">Bevestig mijn e-mailadres</a></p>'
            "<p>Of plak deze link in je browser:<br>"
            f'<a href="{link}">{link}</a></p>'
            "<p>Deze link verloopt over 24 uur. Heb je dit niet aangevraagd? "
            "Dan kun je deze e-mail negeren.</p>"
        )
        text = (
            "Welkom bij Compass.\n\n"
            "Bevestig je e-mailadres om je account te activeren:\n"
            f"{link}\n\n"
            "Deze link verloopt over 24 uur."
        )
        try:
            await self._email_sender.send(
                to=user.email, subject=subject, html=html, text=text
            )
        except Exception:  # noqa: BLE001 — never fail the caller on delivery
            _logger.exception(
                "verification_email_failed", user_id=user.id, trace_id=trace_id
            )

    async def register(
        self, payload: UserCreate, request: Request, response: Response
    ) -> TokenResponse | dict[str, object]:
        """
        Create a new user account.

        With the email-verification gate **active**, the account is created
        unverified, a verification link is emailed, and NO session is issued —
        the caller gets ``{"status": "verification_required"}`` (202) and must
        confirm before logging in. With the gate inactive (Capelle / legacy /
        tests) the original behaviour holds: the account is created verified
        and a session cookie + token are returned immediately.

        Returns 409 if the email is already registered.
        """
        trace_id = TraceContext.new()
        _logger.info("register_attempt", email=payload.email, trace_id=trace_id)
        self._rate_limit(request, payload.email)

        existing = await self._store.get_user_by_email(payload.email)
        if existing:
            raise HTTPException(status_code=409, detail="Email already registered")

        gate_active = self._gate_active(request)
        hashed = self._auth.hash_password(payload.password)
        user = User(
            id=generate_id(),
            email=payload.email,
            hashed_password=hashed,
            # Gate active → unverified until the link is followed. Gate
            # inactive (Capelle host, or feature off) → verified at birth so
            # the account is never gated even if verification is enabled later.
            email_verified=not gate_active,
        )
        await self._store.create_user(user)
        if gate_active:
            await self._send_verification_email(user, request, trace_id)
            response.status_code = 202
            _logger.info(
                "register_verification_required",
                user_id=user.id,
                trace_id=trace_id,
            )
            return {"status": "verification_required", "email": user.email}

        token = self._auth.create_token(user)
        self._set_auth_cookie(response, token)
        return token

    async def login(
        self, payload: UserCreate, request: Request, response: Response
    ) -> TokenResponse:
        """
        Authenticate with email and password, return a JWT.

        Returns 401 if credentials are invalid.  Constant-time password
        comparison prevents timing attacks.
        """
        trace_id = TraceContext.new()
        _logger.info("login_attempt", email=payload.email, trace_id=trace_id)
        self._rate_limit(request, payload.email)

        user = await self._store.get_user_by_email(payload.email)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        if not self._auth.verify_password(payload.password, user.hashed_password):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        # Email-verification gate: a password account must confirm its address
        # before it may hold a session. SSO accounts are exempt (the IdP
        # verified them) and every pre-feature account was grandfathered to
        # verified by the store migration. Credentials are already proven valid
        # here, so revealing "not verified" leaks nothing new about the account.
        if (
            self._gate_active(request)
            and user.auth_source == "password"
            and not user.email_verified
        ):
            _logger.info(
                "login_blocked_unverified", user_id=user.id, trace_id=trace_id
            )
            raise HTTPException(
                status_code=403,
                detail={
                    "code": EMAIL_NOT_VERIFIED_CODE,
                    "message": (
                        "Bevestig eerst je e-mailadres. We hebben je een "
                        "verificatielink gestuurd."
                    ),
                },
            )

        token = self._auth.create_token(user)
        self._set_auth_cookie(response, token)
        return token

    async def verify(self, token: str, request: Request) -> RedirectResponse:
        """
        Confirm an email address from a verification link and log the user in.

        The link is ``GET /api/auth/verify?token=...`` (clicked from an email,
        so it must be a GET that lands somewhere friendly). On success the
        account is marked verified, a session cookie is minted, and the browser
        is redirected into the app. On an invalid/expired token the user is
        bounced to the login page with an error flag so they can re-request.
        Returns 404 when the gate is inactive (the route is dormant).
        """
        if not self._gate_active(request):
            raise HTTPException(status_code=404, detail="Not found")
        assert self._verification_service is not None
        origin = self._request_origin(request)
        try:
            user_id = self._verification_service.verify(token)
        except ValueError as exc:
            _logger.info("verify_token_rejected", reason=str(exc))
            return RedirectResponse(
                url=f"{origin}/login?verified=invalid", status_code=303
            )

        await self._store.set_email_verified(user_id)
        user = await self._store.get_user_by_id(user_id)
        if user is None:  # token valid but user gone — treat as invalid link
            return RedirectResponse(
                url=f"{origin}/login?verified=invalid", status_code=303
            )

        # Log them straight in: set the same session cookie register/login use,
        # then redirect into the app with a success flag for a toast.
        session_token = self._auth.create_token(user)
        redirect = RedirectResponse(url=f"{origin}/?verified=1", status_code=303)
        self._set_auth_cookie(redirect, session_token)
        _logger.info("email_verified", user_id=user.id)
        return redirect

    async def resend_verification(
        self, payload: ResendVerificationRequest, request: Request
    ) -> dict[str, bool]:
        """
        Re-send the verification email for an unverified password account.

        Always returns ``{"ok": True}`` regardless of whether the address
        exists or is already verified, so the endpoint never leaks account
        existence. Rate-limited like the other auth endpoints. Returns 404 when
        the gate is inactive.
        """
        if not self._gate_active(request):
            raise HTTPException(status_code=404, detail="Not found")
        trace_id = TraceContext.new()
        self._rate_limit(request, payload.email)
        user = await self._store.get_user_by_email(payload.email)
        if (
            user is not None
            and user.auth_source == "password"
            and not user.email_verified
        ):
            await self._send_verification_email(user, request, trace_id)
            _logger.info("verification_resent", user_id=user.id, trace_id=trace_id)
        return {"ok": True}

    async def me(self, request: Request) -> dict[str, str]:
        """
        Return the currently-authenticated user's id + email.

        Used by the frontend to hydrate its auth state from the HttpOnly
        cookie on page load without needing to persist user info in
        ``localStorage``. Returns 401 if the cookie is missing or the
        token is invalid/expired.
        """
        token = request.cookies.get(self._settings.auth_cookie_name)
        if not token:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
        if not token:
            raise HTTPException(status_code=401, detail="Not authenticated")
        try:
            user = self._auth.validate_token(token)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return {"user_id": user.id, "email": user.email}

    async def config(self) -> dict[str, object]:
        """
        Public auth-capability descriptor for the frontend.

        Reports whether Entra OIDC SSO is configured so the login screens
        can show or hide the "Sign in with Microsoft" button without a
        rebuild. Carries no secrets — only the boolean gate and, when on,
        the provider name. Unauthenticated by design: the SPA calls this
        before any session exists.
        """
        enabled = self._settings.oidc_enabled
        return {
            "sso_enabled": enabled,
            "sso_provider": "microsoft" if enabled else None,
        }

    async def logout(self, request: Request, response: Response) -> dict[str, bool]:
        """
        Clear the auth cookie and revoke the user's live sockets.

        Clearing the cookie stops the browser replaying the token. In
        addition, when a revocation registry is wired (API-3), the user's
        current revocation instant is advanced to "now" so any live WebSocket
        authenticated with a token issued at/before this moment is closed on
        its next revalidation tick — closing the gap where a logged-out (or
        stolen) token kept streaming until its natural ``exp``.

        Best-effort: an unauthenticated/expired logout still clears the cookie
        and returns ``ok`` without raising.
        """
        if self._revocation is not None:
            token = request.cookies.get(self._settings.auth_cookie_name)
            if not token:
                auth_header = request.headers.get("Authorization", "")
                if auth_header.startswith("Bearer "):
                    token = auth_header[7:]
            if token:
                try:
                    user, _ = self._auth.validate_token_with_iat(token)
                    self._revocation.revoke_before(user.id, time.time())
                    _logger.info("logout_revoked", user_id=user.id)
                except ValueError:
                    # Already-invalid token: nothing to revoke, still log out.
                    pass

        response.delete_cookie(
            key=self._settings.auth_cookie_name,
            path="/",
            secure=self._settings.auth_cookie_secure,
            samesite="lax",
        )
        return {"ok": True}
