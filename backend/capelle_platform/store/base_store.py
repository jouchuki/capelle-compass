"""
Abstract base class for the persistence store.

Defines CRUD operations for users, chat sessions, and messages.
Implementations may use SQLite, PostgreSQL/Supabase, or an in-memory dict.
IO is strictly separated from business logic — handlers call store methods,
never raw SQL.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from capelle_platform.models.message import ChatMessage, MessageStatus
from capelle_platform.models.session import ChatSession
from capelle_platform.models.telemetry import (
    FeedbackEntry,
    FeedbackStatus,
    FeedbackTopic,
    TelemetryEventType,
    UsageEvent,
    UserEngagement,
    UserQuestion,
    UserRecord,
    UserTokenTotals,
)
from capelle_platform.models.user import User
from capelle_platform.web.host import Domain


class BaseStore(ABC):
    """
    Persistence contract for the Capelle Platform.

    All methods are async to support non-blocking IO regardless of the
    underlying driver (aiosqlite, asyncpg, etc.).
    """

    @abstractmethod
    async def initialize(self) -> None:
        """
        Run any one-time setup: create tables, run migrations.

        Called once at application startup by the Builder.
        """

    @abstractmethod
    async def close(self) -> None:
        """Release database connections and resources."""

    # --- Users ---

    @abstractmethod
    async def create_user(self, user: User) -> User:
        """
        Persist a new user record.

        Raises ValueError if the email already exists.
        """

    @abstractmethod
    async def get_user_by_email(self, email: str) -> User | None:
        """Look up a user by email.  Returns None if not found."""

    @abstractmethod
    async def get_user_by_id(self, user_id: str) -> User | None:
        """Look up a user by ID.  Returns None if not found."""

    @abstractmethod
    async def get_user_by_entra(self, oid: str, tid: str) -> User | None:
        """
        Look up an SSO user by their Entra ``(oid, tid)`` identity pair.

        This is the durable re-linkage key for Entra OIDC accounts: a
        user's email can change in the directory, but the object id (oid)
        within a home tenant (tid) is stable. Returns ``None`` when no
        account carries that pair (e.g. a first-time SSO sign-in, which
        the caller then links by email or auto-provisions). Both columns
        must match — a partial match never resolves a user.
        """

    @abstractmethod
    async def link_entra_identity(
        self, user_id: str, oid: str, tid: str
    ) -> None:
        """
        Attach an Entra ``(oid, tid)`` identity to an existing account.

        Used when an SSO sign-in matches a pre-existing user by email:
        the durable oid/tid key is persisted and ``auth_source`` flips to
        ``'sso'`` so a later directory email change still re-links via
        :meth:`get_user_by_entra`. Idempotent.
        """

    async def set_email_verified(self, user_id: str) -> None:
        """
        Mark a user's email address as verified.

        Idempotent: confirming an already-verified account is a no-op. Called
        by the auth handler when a valid verification link is followed.

        Email verification is a Postgres-prod capability; the default raises so
        a backend that does not support it (e.g. the SQLite dev/test store)
        fails loudly rather than silently dropping a confirmation. The Postgres
        store overrides this.
        """
        raise NotImplementedError(
            "set_email_verified is only implemented by the Postgres store"
        )

    @abstractmethod
    async def set_user_daily_limit(
        self, user_id: str, limit: int | None
    ) -> None:
        """
        Persist the per-user daily-analysis override on ``users``.

        Semantics (interpretation lives in the quota enforcer):

        * ``None`` — clear the override; fall back to the global
          ``Settings.daily_analysis_limit``.
        * ``0``    — unlimited; the enforcer never raises for this user.
        * positive — user-specific hard cap.

        Idempotent: the same ``(user_id, limit)`` pair produces the
        same observable state for every subsequent read. The admin
        endpoint handles validation; implementations accept whatever
        they are given.
        """

    # --- Chat Sessions ---

    @abstractmethod
    async def create_session(self, session: ChatSession) -> ChatSession:
        """Persist a new chat session."""

    @abstractmethod
    async def get_session(self, session_id: str) -> ChatSession | None:
        """Fetch a single session by ID."""

    @abstractmethod
    async def list_sessions(
        self, user_id: str, *, domain: Domain | None = None
    ) -> list[ChatSession]:
        """
        List all sessions for a user, newest first.

        When ``domain`` is supplied, filter by the session namespace.
        A ``None`` domain returns all non-deleted sessions for the user.
        """

    @abstractmethod
    async def update_session_title(self, session_id: str, title: str) -> None:
        """Update the display title of a session."""

    @abstractmethod
    async def soft_delete_session(self, session_id: str) -> None:
        """
        Flag ``session_id`` as deleted without removing its row.

        The session and its messages are kept for audit; user-facing
        listings (``list_sessions``) filter the row out. Idempotent:
        re-deleting a session is a no-op.
        """

    @abstractmethod
    async def update_session_topic(self, session_id: str, topic: str) -> None:
        """
        Persist a short (1-3 word) topic tag on a session.

        Topic auto-tagging is best-effort: handlers must catch errors from
        this call so the user-facing flow is never blocked.
        """

    # --- Chat Messages ---

    @abstractmethod
    async def create_message(self, message: ChatMessage) -> ChatMessage:
        """Persist a new chat message."""

    @abstractmethod
    async def get_message(self, message_id: str) -> ChatMessage | None:
        """Fetch a single message by ID."""

    @abstractmethod
    async def list_messages(self, session_id: str) -> list[ChatMessage]:
        """List all messages in a session, oldest first."""

    @abstractmethod
    async def update_message(
        self,
        message_id: str,
        content: str | None = None,
        status: MessageStatus | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """
        Update a message's content, status, or metadata.

        Used by the worker to fill in the assistant response after
        ohrs completes.
        """

    @abstractmethod
    async def search_messages(
        self, user_id: str, query: str, limit: int = 50
    ) -> list[ChatMessage]:
        """
        Full-text search messages across all sessions owned by ``user_id``.

        Returns messages whose content contains ``query`` (case-insensitive),
        newest first, capped at ``limit`` rows. Only messages belonging to
        sessions owned by the supplied user are returned — the store is
        responsible for enforcing that constraint.
        """

    @abstractmethod
    async def claim_message_for_processing(
        self,
        message_id: str,
        *,
        expected_status: MessageStatus,
        claimed_status: MessageStatus,
    ) -> bool:
        """
        Atomically transition a message from ``expected_status`` to
        ``claimed_status``, returning whether THIS caller won the claim.

        Implemented as a single conditional update —
        ``UPDATE chat_messages SET status = :claimed
        WHERE id = :id AND status = :expected`` — and returns ``True``
        only when exactly one row changed. Under at-least-once queue
        delivery a redelivered job will find the message already past
        ``expected_status`` and get ``False`` back, letting the worker
        abort without re-running the analysis. The
        operation is its own committed transaction so the claim is
        durable the instant it returns.
        """

    # --- Daily quota counter (atomic reservation ledger) ---

    @abstractmethod
    async def reserve_daily_quota(
        self, user_id: str, day: str, limit: int
    ) -> bool:
        """
        Atomically reserve one unit of the user's daily quota for ``day``.

        ``day`` is the caller-computed bucket key (``YYYY-MM-DD`` in the
        quota timezone) so the store never needs to know the timezone
        policy. ``limit`` is the user's effective cap for the day.

        Semantics — a single atomic upsert+conditional-increment:

        * If no counter row exists for ``(user_id, day)`` and
          ``limit >= 1``, create it at ``n = 1`` and return ``True``.
        * If a row exists with ``n < limit``, bump it to ``n + 1`` and
          return ``True``.
        * Otherwise (``n >= limit``) leave it untouched and return
          ``False`` — the caller denies the request.

        This is the durable, non-swallowing quota ledger: the reservation
        is committed before the method returns so concurrent
        ``send_message`` calls cannot both pass a one-slot gate. Callers
        that ultimately fail the analysis MUST call
        :meth:`release_daily_quota` to give the slot back.
        """

    @abstractmethod
    async def release_daily_quota(self, user_id: str, day: str) -> None:
        """
        Return one previously-reserved unit of daily quota for ``day``.

        Decrements the ``(user_id, day)`` counter, clamped at zero so a
        double-release can never drive the ledger negative. Idempotent
        below zero. Called on the worker's terminal-failure path so a
        failed analysis does not permanently consume a slot — matching
        the "failed analyses do not count" quota contract.
        """

    @abstractmethod
    async def get_daily_quota_used(self, user_id: str, day: str) -> int:
        """
        Read the reserved-unit count for ``(user_id, day)``.

        Returns ``0`` when no counter row exists. Used by the quota
        enforcer's ``status`` snapshot, which must never mutate state.
        """

    @abstractmethod
    async def fail_stale_messages(
        self,
        *,
        non_terminal: tuple[MessageStatus, ...],
        older_than: datetime,
        failure_content: str,
    ) -> list[tuple[str, str]]:
        """
        Mark every message still in a ``non_terminal`` status whose
        ``created_at`` is older than ``older_than`` as
        :attr:`MessageStatus.FAILED`, returning ``(message_id, user_id)``
        for each row swept.

        Backstops the worker lifecycle: a job published but never
        consumed (crash between publish and claim), or one whose worker
        died mid-run, would otherwise leave the assistant message
        spinning in ``THINKING``/``STREAMING`` forever. A startup +
        periodic reaper calls this with a generous timeout so genuine
        in-flight jobs are never reaped. ``failure_content`` is written
        as the message body so the UI shows a terminal error rather than
        a frozen spinner. The ``user_id`` (resolved via the message's
        session) lets the reaper broadcast a ``message_failed`` event to
        the owning sockets.
        """

    # --- Fork Links (Wave 3 C2) ---

    @abstractmethod
    async def create_fork_token(self, session_id: str) -> tuple[str, str]:
        """
        Create a shareable read-only token for ``session_id``.

        Returns ``(token, expires_at_iso)``. The token is a 32-char urlsafe
        string; it expires 30 days from creation. Multiple tokens may exist
        for the same session — callers decide when to invalidate.
        """

    @abstractmethod
    async def resolve_fork_token(self, token: str) -> ChatSession | None:
        """
        Look up the session referenced by a fork token.

        Returns the ChatSession if the token exists and has not expired,
        else None. No authorisation check — callers that expose session
        payloads via tokens must rely solely on the token's opacity.
        """

    @abstractmethod
    async def clone_session_for_user(
        self, source_session_id: str, new_user_id: str
    ) -> ChatSession:
        """
        Deep-copy a session (title, topic, messages) into ``new_user_id``'s
        account.

        All messages are re-issued with fresh IDs but preserve role,
        content, metadata, status, and original chronological order
        (``created_at``). The returned session has a new ID and a title
        suffixed with " (fork)". The source session is never mutated.
        """

    # --- Telemetry (usage events) ---

    @abstractmethod
    async def insert_usage_event(self, event: UsageEvent) -> None:
        """
        Persist one telemetry event.

        Failures here must not propagate — the caller (a handler on the
        hot path) cannot afford to block on the analytics store. The
        implementation is responsible for logging+swallowing its own
        errors.
        """

    @abstractmethod
    async def count_events_since(
        self,
        user_id: str,
        event_type: TelemetryEventType,
        since: datetime,
    ) -> int:
        """
        Count events of ``event_type`` for ``user_id`` with ``ts >= since``.

        Used by the quota enforcer. ``since`` must be timezone-aware;
        implementations compare ISO-8601 strings which are monotonic for
        any single timezone.
        """

    @abstractmethod
    async def list_usage_events(
        self,
        *,
        event_type: TelemetryEventType | None = None,
        user_id: str | None = None,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[UsageEvent]:
        """
        Admin read helper for the analytics dashboard.

        Filters are all optional; when omitted the store returns the
        ``limit`` newest rows across all users. Implementations MUST
        order newest-first so the admin dashboard can paginate.
        """

    @abstractmethod
    async def count_users_created_since(self, since: datetime) -> int:
        """
        Count user rows whose ``created_at >= since``.

        Used by the admin dashboard to report signup growth.
        """

    @abstractmethod
    async def aggregate_engagement_since(
        self,
        since: datetime,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> list[UserEngagement]:
        """
        Per-user engagement counters scoped to ``[since, now)``.

        Returns users with at least one event in the window, ordered by
        ``last_active`` descending so the most recently active users
        float to the top. Users whose accounts exist but who produced
        no events in the window are intentionally omitted — this
        endpoint is about "who's engaging", not "who registered".

        Pagination is offset-based; callers request ``limit + 1`` rows
        internally to decide whether a "Volgende" link should be shown,
        but the public contract only surfaces the first ``limit`` rows.
        """

    @abstractmethod
    async def list_recent_questions(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> list[UserQuestion]:
        """
        Return recent user-role messages joined with their session +
        owner, newest first.

        Excludes soft-deleted sessions so admins don't see content
        from threads users already cleared. Deleted users' questions
        are also filtered out (FK remains intact but listing such
        rows would surprise ops).
        """

    @abstractmethod
    async def list_users_paginated(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        admin_emails: frozenset[str] = frozenset(),
    ) -> list[UserRecord]:
        """
        Return every user in the system, newest-registered first.

        Unlike ``aggregate_engagement_since`` this joins on users as the
        primary table so accounts with zero events are still returned.
        ``admin_emails`` is consulted server-side to flag admin rows.
        """

    @abstractmethod
    async def sum_tokens_by_user_since(
        self,
        since: datetime,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> list[UserTokenTotals]:
        """
        Aggregate token spend per user for ``analysis_completed`` events
        since ``since``.

        Returns the heaviest users first, capped at ``limit``. Users
        whose analyses have no attached usage (older events from before
        token accounting shipped) contribute zero tokens but still
        count as analyses.
        """

    @abstractmethod
    async def sum_tokens_grouped_by_day(
        self,
        *,
        days: int = 30,
    ) -> list[tuple[str, int]]:
        """
        Per-day total-token counts for the admin dashboard spark chart.

        Only ``analysis_completed`` events are aggregated. Missing days
        are filled with zeros so the UI gets a continuous series.
        """

    @abstractmethod
    async def sum_tokens_breakdown_grouped_by_day(
        self,
        *,
        days: int = 30,
    ) -> list[tuple[str, int, int, int]]:
        """
        Per-day token counts split by flavour: ``(day, input, output, cache)``
        where ``cache`` is the sum of cache_creation_input_tokens +
        cache_read_input_tokens. Drives the admin Overzicht stacked
        token chart. Only ``analysis_completed`` events are aggregated;
        missing days are zero-filled.
        """

    @abstractmethod
    async def count_events_grouped_by_hour(
        self,
        event_type: TelemetryEventType,
        *,
        hours: int = 24,
    ) -> list[tuple[str, int]]:
        """
        Per-hour bucketed counts for ``event_type`` across the trailing
        ``hours`` window.

        Returns ``[(YYYY-MM-DD HH:00, count), ...]`` in ascending order,
        zero-filling empty hours so the frontend renders a continuous
        spark line. Designed for dashboard health charts (e.g.
        ``codex_refresh_failed`` in the last 24h).
        """

    @abstractmethod
    async def count_events_grouped_by_day(
        self,
        event_type: TelemetryEventType,
        *,
        days: int = 30,
    ) -> list[tuple[str, int]]:
        """
        Return a per-day count of the supplied event type over the
        trailing ``days`` window.

        Returns a list of ``(YYYY-MM-DD, count)`` tuples in ascending
        date order. Days with zero events are included as zeros so the
        dashboard can render a continuous time series without gap
        interpolation in the UI.
        """

    # --- Feedback ---

    @abstractmethod
    async def insert_feedback(self, entry: FeedbackEntry) -> None:
        """Persist a feedback submission."""

    @abstractmethod
    async def list_feedback(
        self,
        *,
        status: FeedbackStatus | None = None,
        topic: FeedbackTopic | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[FeedbackEntry]:
        """
        List feedback entries for the admin inbox, newest first.

        Both filters are optional. Pagination is limit-only; the inbox
        is not expected to scale past a few hundred rows during MVP.
        """
