"""
Centralized configuration for the Capelle Platform.

All timeouts, paths, constants, and feature flags live here.
Loaded from environment variables with sensible defaults for local development.
No magic numbers or hardcoded strings anywhere else in the codebase.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings

INSECURE_JWT_DEFAULT = "change-me-in-production"

# Minimum slack (seconds) the job timeout must exceed the elicitation timeout
# by. An outstanding elicitation question must always finish — and the agent
# must have time to consume the answer and emit a turn — before the job
# timeout can fire, otherwise the process is killed mid-question and the
# in-flight answer is discarded (EXEC-4). Sixty seconds matches the
# "Recommended ceiling: job_timeout_seconds − 60" documented on the field.
ELICITATION_JOB_TIMEOUT_MARGIN_SECONDS = 60


class Settings(BaseSettings):
    """
    Single source of truth for every tuneable in the platform.

    Values are read from environment variables (prefixed CAPELLE_) or a .env file.
    Production deployments override via env; local dev uses defaults.
    """

    model_config: ClassVar[dict[str, object]] = {
        "env_prefix": "CAPELLE_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    # --- Server ---
    host: str = Field(default="0.0.0.0", description="Bind address for uvicorn")
    port: int = Field(default=8080, description="Bind port for uvicorn")
    debug: bool = Field(default=False, description="Enable debug mode (never in prod)")
    public_origin: str = Field(
        default="http://localhost:8080",
        description="Canonical public origin (scheme+host[:port]) for user-facing URLs like fork links",
    )

    # --- Auth / JWT ---
    jwt_secret: str = Field(
        default=INSECURE_JWT_DEFAULT,
        description="HMAC secret for signing JWTs. MUST be overridden in prod via CAPELLE_JWT_SECRET.",
        validate_default=True,
    )
    jwt_algorithm: str = Field(default="HS256", description="JWT signing algorithm")
    jwt_expiry_minutes: int = Field(
        default=1440, description="Token validity in minutes (24h default)"
    )
    auth_cookie_name: str = Field(
        default="compass_auth",
        description="Cookie name for session auth token",
    )
    auth_cookie_secure: bool = Field(
        default=True,
        description="Whether the auth cookie requires HTTPS. Override to False only for local dev.",
    )
    internal_hook_secret: str = Field(
        default="",
        description=(
            "Shared secret (API-5) used to authenticate ohrs → platform "
            "internal calls (``/internal/hook`` and ``/internal/ask``) with "
            "an HMAC-SHA256 header, as defence-in-depth beyond the "
            "loopback-host check. The platform mints the header into the "
            "per-job ohrs hook config and the ``capelle-ask`` CLI env; the "
            "InternalHandler verifies it. Empty disables HMAC verification "
            "(loopback guard only) — set CAPELLE_INTERNAL_HOOK_SECRET in "
            "every prod deploy."
        ),
    )
    # --- Entra/Azure AD OIDC SSO (compass-entra-oidc-sso) ---
    # The backend is the confidential OIDC client. SSO is config-gated: a
    # blank client_id OR client_secret leaves ``oidc_enabled`` False and the
    # /api/auth/oidc/* routes 404 — no Azure app means no behaviour change.
    oidc_client_id: str = Field(
        default="",
        description=(
            "Entra application (client) id. Set via CAPELLE_OIDC_CLIENT_ID. "
            "Blank disables SSO."
        ),
    )
    oidc_client_secret: str = Field(
        default="",
        description=(
            "Entra client secret (backend-only — never sent to the frontend). "
            "Set via CAPELLE_OIDC_CLIENT_SECRET. Blank disables SSO."
        ),
    )
    oidc_tenant: str = Field(
        default="organizations",
        description=(
            "Entra authority tenant segment for the authorize/token URLs. "
            "'organizations' accepts any work/school tenant (multi-tenant). "
            "Set CAPELLE_OIDC_TENANT to a tenant id/domain to restrict."
        ),
    )
    oidc_redirect_uri: str = Field(
        default="http://localhost:8080/api/auth/oidc/callback",
        description=(
            "Absolute redirect URI registered on the Entra app; the IdP "
            "302s the auth code back here. Must match the Azure app "
            "registration exactly. Override via CAPELLE_OIDC_REDIRECT_URI "
            "for non-default origins (e.g. data-compass)."
        ),
    )
    oidc_scopes: str = Field(
        default="openid profile email",
        description=(
            "Space-separated OIDC scopes requested at authorize time. "
            "'openid profile email' yields the id_token claims we extract "
            "(email, name, oid, tid)."
        ),
    )
    oidc_addin_completion_url: str = Field(
        default="",
        description=(
            "Where the OIDC callback bounces an *add-in* flow so the final "
            "``Office.context.ui.messageParent`` runs on the SAME origin as "
            "the task pane (an Office Dialog-API hard requirement). The "
            "callback lands on ``oidc_redirect_uri`` (app.example.com), which "
            "is a DIFFERENT domain than the add-in task pane "
            "(addins.example.com) — messageParent from app.example.com is a "
            "silent no-op and the dialog hangs. So for add-in flows the "
            "callback 302s here (the result rides in the URL fragment, never "
            "sent to the server) and this same-origin page does the "
            "messageParent. Must be on the add-in origin; a path under any "
            "deployed host app (e.g. /word/) is fine since same-origin is "
            "origin-, not path-scoped. Override via "
            "CAPELLE_OIDC_ADDIN_COMPLETION_URL."
        ),
    )

    # --- Email verification (compass-email-verification) ---
    # Master switch for the "confirm your email before you can log in" gate.
    # Enable with CAPELLE_EMAIL_VERIFICATION_ENABLED=true. A new password
    # account is created unverified, register sends a verification link
    # instead of a session, and login is refused until the link is clicked.
    # SSO (Entra) accounts are exempt — the IdP already verified the address.
    email_verification_enabled: bool = Field(
        default=False,
        description=(
            "Require a confirmed email before a password account may log in. "
            "Off by default. SSO accounts "
            "and accounts predating the feature are always treated as verified."
        ),
    )
    # Resend (https://resend.com) transactional-email transport. A blank key
    # selects the logging sender (the verification URL is written to the log
    # instead of emailed) so dev/test never need a real provider. The
    # from-address domain MUST be verified in the Resend dashboard or sends
    # 4xx. Override both via CAPELLE_RESEND_API_KEY / CAPELLE_EMAIL_FROM.
    resend_api_key: str = Field(
        default="",
        description=(
            "Resend API key for transactional email. Blank → log the link "
            "instead of sending (dev/test). Secret: set via env, never commit."
        ),
    )
    email_from: str = Field(
        default="Compass <noreply@example.com>",
        description=(
            "RFC-5322 From header for verification emails. The domain must be "
            "a verified sending domain in Resend. Override via CAPELLE_EMAIL_FROM."
        ),
    )
    email_verification_ttl_minutes: int = Field(
        default=1440,
        description="Lifetime of a verification link token in minutes (24h default).",
    )

    expose_hostname_header: bool = Field(default=False)

    @model_validator(mode="after")
    def _require_rabbitmq_url_when_selected(self) -> "Settings":
        """
        When either ``queue`` or ``ws_hub`` is set to ``rabbitmq``,
        ``rabbitmq_url`` MUST be non-empty and not carry the
        ``capelle:capelle`` placeholder password. Same contract as
        the Postgres DSN — fail fast at boot instead of silently
        trying to reach a broker that can't exist.
        """
        needs_broker = self.queue == "rabbitmq" or self.ws_hub == "rabbitmq"
        if needs_broker:
            url = (self.rabbitmq_url or "").strip()
            if not url:
                raise ValueError(
                    "CAPELLE_QUEUE=rabbitmq or CAPELLE_WS_HUB=rabbitmq "
                    "requires CAPELLE_RABBITMQ_URL. Provision with "
                    "scripts/provision-kiyotaka-ijichi.sh and write the "
                    "AMQP URL into /etc/capelle-codex.env."
                )
            if "capelle:capelle@" in url:
                raise ValueError(
                    "CAPELLE_RABBITMQ_URL still uses the placeholder "
                    "'capelle:capelle' password. Rotate the broker "
                    "credential (CAPELLE_RABBITMQ_PASSWORD) before boot."
                )
        return self

    @model_validator(mode="after")
    def _require_postgres_dsn_when_selected(self) -> "Settings":
        """
        When ``store='postgres'`` the DSN MUST be supplied from the env.

        We intentionally keep the ``postgres_dsn`` default empty so a
        misconfigured prod container fails fast at startup instead of
        silently connecting to a placeholder. Also rejects the literal
        placeholder password ``capelle`` — no real deploy should ever
        use the role's own name as its credential.
        """
        if self.store == "postgres":
            dsn = (self.postgres_dsn or "").strip()
            if not dsn:
                raise ValueError(
                    "CAPELLE_STORE=postgres requires CAPELLE_POSTGRES_DSN. "
                    "Provision the password with scripts/provision-aoi-todo.sh "
                    "and write the full DSN into /etc/capelle-codex.env."
                )
            if "capelle:capelle@" in dsn:
                raise ValueError(
                    "CAPELLE_POSTGRES_DSN still uses the placeholder "
                    "'capelle:capelle' password. Generate a real password "
                    "(CAPELLE_PG_PASSWORD) and reconfigure aoi-todo before boot."
                )
        return self

    @model_validator(mode="after")
    def _require_elicitation_within_job_timeout(self) -> "Settings":
        """
        Enforce ``elicitation_timeout_seconds <= job_timeout_seconds - margin``
        for every mode (global, groeikern, jeugdzorg) and validate the reaper
        margin is strictly above 1.0.

        The two timers are otherwise independent (EXEC-4): a question left
        on-screen for the full elicitation window can push cumulative
        wall-clock past the job timeout, killing the agent mid-question and
        discarding the in-flight answer. Requiring a fixed margin
        (:data:`ELICITATION_JOB_TIMEOUT_MARGIN_SECONDS`) below the job
        timeout guarantees the agent always has time to consume the answer
        and produce a final turn. Fails fast at boot on misconfiguration.
        """
        pairs = [
            ("global", self.job_timeout_seconds, self.elicitation_timeout_seconds),
            ("groeikern", self.ohrs_groeikern_job_timeout_seconds,
             self.ohrs_groeikern_elicitation_timeout_seconds),
            ("jeugdzorg", self.ohrs_jeugdzorg_job_timeout_seconds,
             self.ohrs_jeugdzorg_elicitation_timeout_seconds),
        ]
        for name, job_to, elicit in pairs:
            ceiling = job_to - ELICITATION_JOB_TIMEOUT_MARGIN_SECONDS
            if elicit > ceiling:
                raise ValueError(
                    f"[{name}] elicitation timeout ({elicit}) must be at most "
                    f"job_timeout − {ELICITATION_JOB_TIMEOUT_MARGIN_SECONDS} "
                    f"(= {ceiling}) so an outstanding question cannot outlive "
                    "the job timeout. Lower the elicitation window or raise "
                    "the job timeout for this mode."
                )
        if self.reaper_stale_timeout_margin <= 1.0:
            raise ValueError(
                "reaper_stale_timeout_margin "
                f"(CAPELLE_REAPER_STALE_TIMEOUT_MARGIN = "
                f"{self.reaper_stale_timeout_margin}) must be > 1.0, else the "
                "reaper would sweep still-running jobs."
            )
        return self

    @field_validator("jwt_secret")
    @classmethod
    def _reject_insecure_jwt_secret(cls, v: str) -> str:
        if v == INSECURE_JWT_DEFAULT:
            raise ValueError(
                "CAPELLE_JWT_SECRET is still the insecure default. "
                "Set a strong random secret (32+ bytes) before starting the service."
            )
        if len(v) < 32:
            raise ValueError(
                "CAPELLE_JWT_SECRET must be at least 32 characters. "
                "Generate with: python -c 'import secrets; print(secrets.token_urlsafe(48))'"
            )
        return v

    # --- Database ---
    store: Literal["sqlite", "postgres"] = Field(
        default="sqlite",
        description=(
            "Which BaseStore implementation the Builder wires. "
            "'sqlite' is the MVP default; 'postgres' is the shared "
            "aoi-todo container path. Override via CAPELLE_STORE."
        ),
    )
    sqlite_path: Path = Field(
        default=Path("compass.db"),
        description="Path to SQLite database file (used when store='sqlite')",
    )
    postgres_dsn: str = Field(
        default="",
        description=(
            "asyncpg DSN for the Postgres store. MUST be supplied via "
            "CAPELLE_POSTGRES_DSN in the env file on every yuta when "
            "store='postgres' — never hard-coded, because it carries "
            "the DB password. The shape is "
            "``postgresql://capelle:<password>@aoi-todo:5432/capelle`` "
            "with the password minted by ``provision-aoi-todo.sh``."
        ),
    )

    # --- Queue + WS fanout (horizontal scale) ---
    queue: Literal["memory", "rabbitmq"] = Field(
        default="memory",
        description=(
            "BasePublisher/BaseConsumer implementation. 'memory' is a "
            "single-process asyncio.Queue (MVP); 'rabbitmq' pushes jobs "
            "to a shared kiyotaka-ijichi broker so any yuta can drain "
            "them. Override via CAPELLE_QUEUE."
        ),
    )
    ws_hub: Literal["memory", "rabbitmq"] = Field(
        default="memory",
        description=(
            "BaseWebSocketHub implementation. 'memory' is process-local "
            "(fine for single yuta); 'rabbitmq' uses a fanout exchange "
            "so a broadcast from yuta-B reaches the WS connection a "
            "user opened to yuta-A. Override via CAPELLE_WS_HUB."
        ),
    )
    rabbitmq_url: str = Field(
        default="",
        description=(
            "AMQP URL for the shared RabbitMQ broker (kiyotaka-ijichi). "
            "Required when queue='rabbitmq' or ws_hub='rabbitmq'. Shape: "
            "``amqp://capelle:<password>@kiyotaka-ijichi:5672/capelle``. "
            "Supply via CAPELLE_RABBITMQ_URL in the env file — never "
            "hard-coded, same contract as the Postgres DSN."
        ),
    )
    chroma_http: str = Field(
        default="127.0.0.1:8000",
        description=(
            "host:port of the Chroma service. Defaults to loopback "
            "(Chroma inside the same container); point at "
            "yuji-itadori:8000 in multi-yuta deployments."
        ),
    )

    # --- Worker ---
    worker_concurrency: int = Field(
        default=5,
        description=(
            "Max concurrent ohrs agents this yuta runs. Also used as the "
            "RabbitMQ prefetch count so a single yuta never pulls more "
            "work than it can run. Fleet-wide capacity = "
            "worker_concurrency × number of yutas."
        ),
    )
    reaper_stale_timeout_margin: float = Field(
        default=1.25,
        description="Stale-message reaper threshold = max per-mode job "
        "timeout × this margin. Must be > 1.0 so live jobs are never reaped.",
    )
    reaper_interval_seconds: float = Field(
        default=60.0, description="How often the stale-message reaper sweeps."
    )
    job_timeout_seconds: int = Field(
        default=600, description="Max seconds per ohrs job (10 min)"
    )
    elicitation_timeout_seconds: int = Field(
        default=300,
        description=(
            "Seconds the platform waits for a user to answer a mid-run "
            "elicitation question before returning an empty answer to the "
            "agent. Must be comfortably below job_timeout_seconds (default "
            "600) so the agent is not killed while a question is still "
            "on-screen. Recommended ceiling: job_timeout_seconds − 60."
        ),
    )

    # --- ohrs executor ---
    ohrs_binary: str = Field(
        default="ohrs",
        description="Path to the ohrs binary",
    )
    ohrs_max_turns: int = Field(
        default=20,
        description="Max turns per ohrs agent invocation",
    )

    # --- Per-mode job / turns / elicitation budgets ---
    ohrs_groeikern_job_timeout_seconds: int = Field(
        default=3300,
        description="Max seconds per groeikern ohrs job (55 min); deep "
        "question-cascades with subagent fan-out + a verifier pass.",
    )
    ohrs_groeikern_max_turns: int = Field(
        default=160, description="Max turns for a groeikern ohrs agent."
    )
    ohrs_groeikern_elicitation_timeout_seconds: int = Field(
        default=600, description="User answer window for groeikern (10 min)."
    )
    ohrs_jeugdzorg_job_timeout_seconds: int = Field(
        default=900, description="Max seconds per jeugdzorg ohrs job (15 min)."
    )
    ohrs_jeugdzorg_max_turns: int = Field(
        default=20, description="Max turns for a jeugdzorg ohrs agent."
    )
    ohrs_jeugdzorg_elicitation_timeout_seconds: int = Field(
        default=300, description="User answer window for jeugdzorg (5 min)."
    )
    ohrs_working_dir: Path = Field(
        default=Path("/opt/compass/agent-workspace"),
        description=(
            "Working directory for ohrs in *groeikern* mode (where the "
            "multi-source Capelle skills + CLI tools live). Kept under the "
            "legacy name for backward compatibility; new code reads it via "
            "the GroeikernModeConfig."
        ),
    )
    ohrs_working_dir_jeugdzorg: Path = Field(
        default=Path("/opt/compass/jeugdzorg-workspace"),
        description=(
            "Working directory for ohrs in *jeugdzorg* mode (where the 3 "
            "synthetic CSVs + capelle-jeugdzorg CLI live). Only this "
            "directory is mounted into a jeugdzorg job; the multi-source "
            "groeikern data is invisible."
        ),
    )
    ohrs_tools_bin: Path = Field(
        default=Path("/opt/compass/tools/bin"),
        description=(
            "Directory holding the agent CLI binaries (capelle-ask, cbs, "
            "capelle-beleid, capelle-budget, capelle-buitenbeter, "
            "capelle-cube, groeikernen). Prepended to the groeikern agent's "
            "PATH so the skills can invoke the tools bare-name. In production "
            "the tools live in a shared venv at /opt/tools-venv/bin (relocated "
            "from <ohrs_working_dir>/venv/bin on 2026-06-02); override with "
            "CAPELLE_OHRS_TOOLS_BIN for a different layout (e.g. a local-dev "
            "checkout's venv)."
        ),
    )
    ohrs_jobs_dir: Path = Field(
        default=Path("/tmp/compass-jobs"),
        description="Temp directory for per-job ohrs data isolation",
    )
    ohrs_home_dir: Path = Field(
        default=Path("/opt/compass/agent-home"),
        description=(
            "HOME directory the ohrs subprocess sees. Production deploys "
            "stage read-only reference data here (notably "
            "~/.local/share/cbs-tool/{catalog.json,municipalities.json,...} "
            "for the groeikern mode) and the global "
            "~/.openharnessrs/settings.json fallback for provider/model "
            "config. For local dev override with CAPELLE_OHRS_HOME_DIR=$HOME "
            "so ohrs picks up your personal config + caches."
        ),
    )

    # --- Observability ---
    log_level: str = Field(default="INFO", description="Logging level")
    log_format: str = Field(
        default="json", description="Log format: 'json' or 'text'"
    )

    # --- Feature flags ---
    graph_enabled: bool = Field(
        default=True,
        description=(
            "Enable the live finding-graph (analysis_graphs store, internal write "
            "endpoints, WS graph_delta events). When False, all graph endpoints "
            "return 404 and no WS events are emitted; the platform behaves exactly "
            "as before the finding-graph feature was introduced. Override via "
            "CAPELLE_GRAPH_ENABLED=false to revert without a code deploy."
        ),
    )

    # --- Langfuse trajectory export ---
    langfuse_enabled: bool = Field(
        default=False,
        description="Export OHRS trajectories to Langfuse when true.",
    )
    langfuse_host: str = Field(
        default="",
        description="Langfuse base URL, e.g. http://langfuse:3000",
    )
    langfuse_public_key: str = Field(
        default="",
        description="Langfuse public API key (pk-lf-...).",
    )
    langfuse_secret_key: str = Field(
        default="",
        description="Langfuse secret API key (sk-lf-...).",
    )

    # --- Quota / Telemetry / Feedback ---
    daily_analysis_limit: int = Field(
        default=3,
        description=(
            "Maximum number of finished analyses a single account may run per "
            "calendar day (in ``quota_timezone``). Failed analyses do not count."
        ),
    )
    quota_timezone: str = Field(
        default="Europe/Amsterdam",
        description=(
            "IANA timezone whose midnight resets the per-user daily quota. "
            "Amsterdam matches the municipal working-day mental model."
        ),
    )
    admin_emails: str = Field(
        default="",
        description=(
            "Comma-separated allowlist of admin email addresses. Users whose "
            "token email is in this list may read the admin analytics routes. "
            "Access as a frozenset via ``admin_email_set``."
        ),
    )
    addin_origins: list[str] = Field(
        default=[],
        description=(
            "Origins of the Office add-in task panes + dialog login page "
            "(served from addins.example.com). Unlike the Vite dev origins "
            "these are allowed in *every* environment, not just debug: "
            "add-ins authenticate with an ``Authorization: Bearer`` header "
            "(not the HttpOnly cookie), so the add-in origin must be a "
            "trusted CORS origin in production too. Override the whole list "
            "via CAPELLE_ADDIN_ORIGINS (JSON array or comma-separated)."
        ),
    )
    trusted_proxies: str = Field(
        default="",
        description=(
            "Comma-separated list of proxy IPs we trust to set "
            "``X-Forwarded-For``. The nginx LB (masamichi-yaga) belongs here. "
            "Access as a frozenset via ``trusted_proxy_set``."
        ),
    )
    support_email: str = Field(
        default="v.sokolovs@outlook.com",
        description=(
            "Public support address shown to users in the feedback flow. "
            "Also the default 'owner' rendered for the feedback inbox."
        ),
    )

    @property
    def oidc_enabled(self) -> bool:
        """
        True iff Entra OIDC SSO is fully configured.

        Both the client id and the client secret must be non-empty
        (whitespace-trimmed) — a confidential client cannot run the
        auth-code exchange without either. When False the OIDC routes
        return 404 and ``/api/auth/config`` reports ``sso_enabled=False``
        so the frontend hides the "Sign in with Microsoft" button.
        """
        return bool(self.oidc_client_id.strip()) and bool(
            self.oidc_client_secret.strip()
        )

    @property
    def cors_allowed_origins(self) -> list[str]:
        """
        Resolved CORS allow-list (API-2).

        Always allows the canonical :attr:`public_origin` and the Office
        add-in origins (:attr:`addin_origins`). The add-in origins are
        included in *every* environment — they authenticate with a Bearer
        header rather than the credentialed cookie, so they must be a trusted
        origin in prod too, not just under ``debug``. In ``debug`` mode the
        local Vite dev-server origins are additionally added so a developer's
        browser can hit the API cross-origin; in prod those are excluded so
        only the real public + add-in origins are trusted credentialed
        origins. Order-preserving and de-duplicated.
        """
        origins: list[str] = [self.public_origin.rstrip("/")]
        origins.extend(o.rstrip("/") for o in self.addin_origins)
        if self.debug:
            origins.extend(
                ("http://localhost:5173", "http://127.0.0.1:5173")
            )
        seen: set[str] = set()
        unique: list[str] = []
        for origin in origins:
            if origin and origin not in seen:
                seen.add(origin)
                unique.append(origin)
        return unique

    @property
    def admin_email_set(self) -> frozenset[str]:
        """
        Normalised admin allowlist.

        Split on commas, strip whitespace, drop empties, lowercase so
        the check is case-insensitive. Frozen so callers cannot mutate
        a singleton settings instance at runtime.
        """
        raw = self.admin_emails or ""
        return frozenset(
            item.strip().lower()
            for item in raw.split(",")
            if item.strip()
        )

    @property
    def trusted_proxy_set(self) -> frozenset[str]:
        """
        Normalised trusted-proxy allowlist.

        Used by :class:`ClientIpResolver` to decide whether
        ``X-Forwarded-For`` should be believed. Empty set = trust no
        upstream (direct-to-uvicorn deployment).
        """
        raw = self.trusted_proxies or ""
        return frozenset(
            item.strip()
            for item in raw.split(",")
            if item.strip()
        )

    @property
    def max_job_timeout_seconds(self) -> int:
        """Largest per-mode job ceiling — the reaper must sit above this."""
        return max(
            self.job_timeout_seconds,
            self.ohrs_groeikern_job_timeout_seconds,
            self.ohrs_jeugdzorg_job_timeout_seconds,
        )

    @property
    def reaper_stale_threshold_seconds(self) -> float:
        """Age past which a non-terminal message is swept FAILED."""
        return self.max_job_timeout_seconds * self.reaper_stale_timeout_margin
