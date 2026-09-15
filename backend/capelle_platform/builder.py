"""
Application Builder — dependency injection via the Builder pattern.

Constructs every component in the correct order, wires dependencies,
and produces a fully configured FastAPI application.  This is the only
place where concrete implementations are selected — the rest of the
codebase depends only on ABCs.

To swap an implementation (e.g., SQLite -> Supabase), change one line here.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from capelle_platform.auth.email_verification import EmailVerificationService
from capelle_platform.auth.impl_jwt import JWTAuthenticator
from capelle_platform.email.base_sender import BaseEmailSender
from capelle_platform.email.impl_logging import LoggingEmailSender
from capelle_platform.email.impl_resend import ResendEmailSender
from capelle_platform.graph.base_store import BaseGraphStore
from capelle_platform.graph.impl_memory import InMemoryGraphStore
from capelle_platform.auth.revocation import InMemoryTokenRevocationRegistry
from capelle_platform.elicitation.base_registry import BaseElicitationRegistry
from capelle_platform.elicitation.impl_memory import InMemoryElicitationRegistry
from capelle_platform.executor.impl_ohrs import OhrsExecutor
from capelle_platform.handlers.admin import AdminHandler
from capelle_platform.handlers.analysis import AnalysisHandler
from capelle_platform.handlers.auth import AuthHandler
from capelle_platform.handlers.chat import ChatHandler
from capelle_platform.handlers.health import HealthHandler
from capelle_platform.handlers.internal import InternalHandler
from capelle_platform.handlers.oidc import OidcDiscoveryClient, OidcHandler
from capelle_platform.handlers.telemetry import TelemetryHandler
from capelle_platform.handlers.ws import WSHandler
from capelle_platform.observability import get_logger
from capelle_platform.observability.client import LangfuseClient
from capelle_platform.observability.exporter import LangfuseExporter
from capelle_platform.queue.base_queue import BaseConsumer, BasePublisher
from capelle_platform.queue.impl_memory import MemoryConsumer, MemoryPublisher
from capelle_platform.quota.impl_daily import DailyAnalysisQuotaEnforcer
from capelle_platform.settings import Settings
from capelle_platform.store.base_store import BaseStore
from capelle_platform.store.impl_sqlite import SQLiteStore
from capelle_platform.telemetry.impl_default import StoreBackedTelemetryRecorder
from capelle_platform.worker.pool import AsyncWorkerPool
from capelle_platform.ws.base_hub import BaseWebSocketHub
from capelle_platform.ws.impl_memory_hub import InMemoryWebSocketHub

_logger = get_logger(__name__)


class AppBuilder:
    """
    Assembles the FastAPI application with all dependencies wired.

    Usage:
        builder = AppBuilder(settings)
        app = builder.build()

    The Builder owns the lifecycle — it initialises resources in the
    lifespan context manager and tears them down on shutdown.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """
        Initialise the builder with optional settings override.

        Args:
            settings: Application settings.  If None, loaded from env.
        """
        self._settings = settings or Settings()

    def build(self) -> FastAPI:
        """
        Construct and return the fully wired FastAPI application.

        Creates all components, registers routes, and attaches the
        lifespan handler for startup/shutdown.
        """
        settings = self._settings

        # --- Topology guards (STORE-3 / STORE-7) ---
        # The in-memory queue is non-durable and silently single-flight: a
        # crash loses every queued job and `worker_concurrency` is effectively
        # 1. It is a dev/single-process convenience only — forbid it whenever
        # the deployment is otherwise scaled out (a real broker or a shared WS
        # fanout implies more than one process draining work). STORE-7.
        if settings.queue == "memory" and (
            settings.ws_hub == "rabbitmq" or settings.store == "postgres"
        ):
            raise ValueError(
                "CAPELLE_QUEUE=memory is dev/single-process only and is "
                "non-durable single-flight. This deployment is configured for "
                "scale-out (ws_hub=rabbitmq or store=postgres); set "
                "CAPELLE_QUEUE=rabbitmq so jobs survive a crash and "
                "worker_concurrency is honoured."
            )
        # A concurrent worker+API topology (anything other than the memory
        # queue) requires the Postgres store: the SQLite store shares one
        # connection with per-call autocommit across the API and the worker,
        # which is structurally unsafe under concurrency. STORE-3.
        if settings.queue != "memory" and settings.store != "postgres":
            raise ValueError(
                "CAPELLE_STORE=sqlite is dev/single-process only. A concurrent "
                f"queue (CAPELLE_QUEUE={settings.queue}) requires "
                "CAPELLE_STORE=postgres — the SQLite store is not safe to share "
                "between the API handlers and the worker pool. Set "
                "CAPELLE_STORE=postgres (provision aoi-todo) or run the "
                "single-process memory queue for local dev."
            )

        # --- Singletons (heavy resources, one per process) ---
        authenticator = JWTAuthenticator(settings)
        # The store implementation is picked at boot by settings.store —
        # 'sqlite' (MVP default) vs. 'postgres' (shared aoi-todo). Both
        # implement BaseStore, so nothing downstream needs to know.
        store: BaseStore
        if settings.store == "postgres":
            from capelle_platform.store.impl_postgres import PostgresStore
            store = PostgresStore(settings)
        else:
            store = SQLiteStore(settings)
        # Queue: 'memory' is the single-yuta default; 'rabbitmq' lets
        # any yuta in the fleet drain jobs from a shared broker.
        publisher: BasePublisher
        consumer: BaseConsumer
        if settings.queue == "rabbitmq":
            from capelle_platform.queue.impl_rabbitmq import (
                RabbitMQConsumer,
                RabbitMQPublisher,
            )
            publisher = RabbitMQPublisher(settings)
            consumer = RabbitMQConsumer(settings)
        else:
            publisher = MemoryPublisher()
            consumer = MemoryConsumer()

        # WS hub: 'memory' keeps broadcasts process-local; 'rabbitmq'
        # fans out over a capelle.ws exchange so a broadcast published
        # by yuta-B reaches WS connections owned by yuta-A.
        ws_hub: BaseWebSocketHub
        if settings.ws_hub == "rabbitmq":
            from capelle_platform.ws.impl_rabbitmq_hub import (
                RabbitMQWebSocketHub,
            )
            ws_hub = RabbitMQWebSocketHub(settings)
        else:
            ws_hub = InMemoryWebSocketHub()
        recorder = StoreBackedTelemetryRecorder(store)
        quota = DailyAnalysisQuotaEnforcer(store, settings)

        # --- Finding-graph store (additive, feature-flagged) ---
        # Keyed by settings.store to use the same backend as the chat store.
        # PostgresGraphStore writes to analysis_graphs (CREATE IF NOT EXISTS).
        # InMemoryGraphStore is used for sqlite / test environments.
        graph_store: BaseGraphStore
        if settings.store == "postgres":
            from capelle_platform.graph.impl_postgres import PostgresGraphStore
            graph_store = PostgresGraphStore(settings)
        else:
            graph_store = InMemoryGraphStore()

        # --- Langfuse exporter (optional, disabled by default) ---
        langfuse_exporter = None
        if settings.langfuse_enabled:
            langfuse_exporter = LangfuseExporter(
                LangfuseClient(
                    settings.langfuse_host,
                    settings.langfuse_public_key,
                    settings.langfuse_secret_key,
                ),
                jobs_dir=settings.ohrs_jobs_dir,
                enabled=True,
            )

        # --- Worker pool + executor (circular dep: pool provides callback to executor) ---
        # Build pool first without executor, then wire executor with pool's callback
        worker_pool = AsyncWorkerPool(
            consumer=consumer,
            executor=None,  # type: ignore[arg-type] — set below
            store=store,
            ws_hub=ws_hub,
            concurrency=settings.worker_concurrency,
            recorder=recorder,
            # Wire the quota enforcer so a failed job releases its reserved
            # slot, and the job timeout so the stale-message reaper derives a
            # correct threshold (Wave-1 store/quota work).
            quota=quota,
            # Analysis usage is governed by the daily quota.
            exporter=langfuse_exporter,
            job_timeout_seconds=settings.job_timeout_seconds,
            stale_timeout_margin=settings.reaper_stale_timeout_margin,
            reaper_base_timeout_seconds=settings.max_job_timeout_seconds,
            reaper_interval_seconds=settings.reaper_interval_seconds,
        )

        executor = OhrsExecutor(
            settings=settings,
            progress_callback=worker_pool.progress_callback,
            # Inject the graph store so node-scoped follow-up messages
            # (job.node_id set) get the FOCUS NODE block prepended to the
            # prompt.  When graph_enabled is False the executor's
            # _build_prompt_async guard short-circuits before touching the
            # store, so there is no observable difference at runtime.
            graph_store=graph_store,
        )
        worker_pool._executor = executor

        # --- Elicitation registry (singleton shared between internal + analysis handlers) ---
        # One registry per process; holds asyncio Futures for pending mid-run
        # questions. The InternalHandler (localhost-only) creates questions and
        # waits on Futures; the AnalysisHandler (authenticated) resolves them.
        # Behind the round-robin LB the answer POST can land on a different
        # yuta than the one holding the Future, so multi-instance deployments
        # use the RabbitMQ fanout registry (answers reach the owning yuta);
        # single-process keeps the in-memory one.
        elicitation_registry: BaseElicitationRegistry
        if settings.ws_hub == "rabbitmq":
            from capelle_platform.elicitation.impl_rabbitmq import (
                RabbitMQElicitationRegistry,
            )

            elicitation_registry = RabbitMQElicitationRegistry(settings)
        else:
            elicitation_registry = InMemoryElicitationRegistry()

        # --- Token revocation registry (API-3) ---
        # Process-local per-user "valid-after" store. Logout records a
        # revocation instant; the WS handler closes any live socket whose
        # token pre-dates it, so a logout/forced-logout kills the stream
        # before the JWT's natural expiry. Multi-instance (cross-yuta)
        # revocation needs shared state and is deferred — the ABC lets it
        # drop in without touching the handlers.
        revocation_registry = InMemoryTokenRevocationRegistry()

        # --- Handlers (decision layer) ---
        health_handler = HealthHandler()
        # Email-verification collaborators. The sender is chosen by config: a
        # Resend key selects the real transport, otherwise the logging sender
        # (dev/test — the link goes to the log). The handler only *activates*
        # the gate when settings.email_verification_enabled is also set, so
        # wiring these unconditionally is safe (no behaviour change when off).
        verification_service = EmailVerificationService(settings)
        email_sender: BaseEmailSender
        if settings.resend_api_key:
            email_sender = ResendEmailSender(
                api_key=settings.resend_api_key,
                email_from=settings.email_from,
            )
        else:
            email_sender = LoggingEmailSender()
        auth_handler = AuthHandler(
            authenticator,
            store,
            settings,
            revocation=revocation_registry,
            email_sender=email_sender,
            verification_service=verification_service,
        )

        # --- Entra OIDC SSO handler (config-gated) ---
        # Only constructed + registered when both client id and secret are
        # present (settings.oidc_enabled). When disabled the routes simply
        # do not exist — no Azure app means no behaviour change. The handler
        # owns no network state itself; it shares a process-wide
        # httpx.AsyncClient (closed in the lifespan teardown) wrapped by the
        # discovery/JWKS/token-exchange client.
        oidc_handler: OidcHandler | None = None
        oidc_http_client: httpx.AsyncClient | None = None
        if settings.oidc_enabled:
            oidc_http_client = httpx.AsyncClient(timeout=10.0)
            oidc_handler = OidcHandler(
                authenticator,
                store,
                settings,
                discovery=OidcDiscoveryClient(oidc_http_client),
                set_auth_cookie=auth_handler._set_auth_cookie,
            )
        chat_handler = ChatHandler(
            store,
            publisher,
            authenticator,
            settings,
            quota,
            recorder,
        )
        ws_handler = WSHandler(
            authenticator, ws_hub, settings, revocation=revocation_registry
        )
        internal_handler = InternalHandler(
            ws_hub,
            store,
            hook_seen_for=executor.hook_seen_for,
            make_dedup_key=OhrsExecutor.make_dedup_key,
            registry=elicitation_registry,
            settings=settings,
        )
        analysis_handler = AnalysisHandler(
            store,
            publisher,
            authenticator,
            settings,
            registry=elicitation_registry,
        )
        telemetry_handler = TelemetryHandler(
            authenticator, store, recorder, quota, settings
        )
        admin_handler = AdminHandler(authenticator, store, settings, recorder)

        # --- Graph handlers (feature-flagged via settings.graph_enabled) ---
        # We always register the routes — they return 404 inline when the flag
        # is off. This keeps the surface area discoverable for ops without
        # exposing any real data or accepting writes when disabled.
        from capelle_platform.graph.service import GraphService
        from capelle_platform.handlers.graph_internal import GraphInternalHandler
        from capelle_platform.handlers.graph_read import GraphReadHandler

        async def _graph_broadcast(event: dict) -> None:
            """
            Fan a graph_delta event to the session's owning user.

            Resolves session_id (carried in the event) → user_id via the
            chat store, then delegates to the WS hub. Failures are swallowed
            by the GraphService._emit wrapper — a dropped WS event must never
            surface as an HTTP error.
            """
            session_id: str = event.get("session_id", "")
            if not session_id:
                return
            try:
                session = await store.get_session(session_id)
                if session is None:
                    return
                await ws_hub.broadcast_to_user(session.user_id, event)
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "graph_broadcast_failed",
                    session_id=session_id,
                )

        graph_service = GraphService(store=graph_store, broadcast=_graph_broadcast)
        graph_internal_handler = GraphInternalHandler(
            service=graph_service,
            store=store,
            settings=settings,
        )
        graph_read_handler = GraphReadHandler(
            service=graph_service,
            store=store,
            authenticator=authenticator,
            settings=settings,
        )

        # --- FastAPI app ---
        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            """Manage startup and shutdown of async resources."""
            _logger.info("app_starting")
            await store.initialize()
            await graph_store.initialize()
            await ws_hub.start()
            await elicitation_registry.start()
            await worker_pool.start()
            _logger.info("app_ready", port=settings.port)
            yield
            _logger.info("app_shutting_down")
            await worker_pool.stop()
            await elicitation_registry.stop()
            await ws_hub.stop()
            await publisher.close()
            await consumer.close()
            await store.close()
            await graph_store.close()
            if oidc_http_client is not None:
                await oidc_http_client.aclose()
            _logger.info("app_stopped")

        app = FastAPI(
            title="Compass",
            description="Interactive municipal data analysis",
            version="1.1.0",
            lifespan=lifespan,
            # Disable the public OpenAPI + Swagger/ReDoc surfaces. They
            # leak every internal route name + schema (incl. admin
            # endpoints) to any unauthenticated visitor. Ops can still
            # introspect via direct curl against the running service.
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
        )

        # --- CORS (API-2) ---
        # Drive the allow-list from settings: the real public origin in prod,
        # plus the local Vite dev origins only when debug is on. Because we
        # allow credentials, the method/header wildcards are replaced with
        # explicit lists — a credentialed wildcard origin is exactly the
        # shape this finding closes.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

        # --- X-Capelle-Hostname response header (API-7) ---
        # Stamp every response with the yuta that served it. Purely for LB
        # traceability during scale-out verification (scripts/verify-v1.1.sh
        # reads this header to confirm round-robin across the fleet); NOT a
        # general-purpose debugging surface. Gated behind a dedicated flag
        # (default OFF) so an internal hostname is never disclosed on
        # unauthenticated/SPA responses in prod. Local imports mirror the
        # pattern used by the banned-docs routes below — keep the module top
        # small.
        if settings.expose_hostname_header:
            import os  # local: used only by this middleware
            import socket  # local: used only by this middleware

            _CAPELLE_HOSTNAME = (
                os.environ.get("CAPELLE_HOSTNAME") or socket.gethostname()
            )

            @app.middleware("http")
            async def _stamp_hostname(request, call_next):  # type: ignore[no-untyped-def]
                response = await call_next(request)
                response.headers["X-Capelle-Hostname"] = _CAPELLE_HOSTNAME
                return response

        # --- Register routes ---
        app.include_router(health_handler.router)
        app.include_router(auth_handler.router)
        # Entra OIDC SSO routes — only when configured.
        if oidc_handler is not None:
            app.include_router(oidc_handler.router)
        app.include_router(chat_handler.router)
        app.include_router(ws_handler.router)
        app.include_router(internal_handler.router)
        app.include_router(graph_internal_handler.router)
        app.include_router(graph_read_handler.router)
        app.include_router(analysis_handler.router)
        app.include_router(telemetry_handler.router)
        app.include_router(admin_handler.router)
        # The Office Dialog API login page. Registered before the SPA static
        # mount so /addin-login serves the dialog HTML rather than falling
        # through to the React index.html catch-all.

        # Explicit 404 for the usual FastAPI discovery paths. Without
        # these, the SPA static mount catches the request and returns
        # index.html, which is confusing — browsers would render the
        # React app at /docs. A crisp 404 makes the intent obvious to
        # any operator or scanner probing the surface.
        from fastapi import Response  # local import: narrow use, keep top small

        async def _banned_docs_route() -> Response:
            """Permanently-disabled FastAPI discovery route."""
            return Response(status_code=404)

        for banned_path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
            app.add_api_route(
                banned_path,
                _banned_docs_route,
                methods=["GET"],
                include_in_schema=False,
            )

        # --- Static frontend (SPA) ---
        # The SPA uses client-side routing (e.g. /f/<token> for fork links),
        # so any non-API path must serve index.html. ``static_mount`` is a
        # thin helper that lives next to this file; if the bundle directory
        # doesn't exist (e.g. dev runs without ``npm run build``) the helper
        # is a no-op — we never fail startup on a missing dist.
        try:
            from capelle_platform.static_mount import mount_frontend
            mount_frontend(app)
        except ImportError:
            # static_mount is optional; skip silently in environments
            # that don't ship the frontend alongside the backend.
            pass

        return app
