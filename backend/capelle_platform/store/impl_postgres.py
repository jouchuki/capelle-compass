"""
PostgreSQL persistence implementation using asyncpg.

Production-grade BaseStore backed by a shared Postgres instance (the
``aoi-todo`` container in our infra). Behaviourally identical to
``SQLiteStore`` from the caller's perspective — same method shapes,
same return models, same error semantics — so handlers never see the
difference. Picked at runtime by ``AppBuilder`` via ``settings.store``.

Design notes
------------
* Connection pool lives on ``self._pool``; ``initialize()`` creates
  it and bootstraps the schema from ``schema.sql``; ``close()`` drains
  it. There is no shared mutable state on ``self`` — the pool is the
  sole concurrency primitive, so the store is safe to call from any
  task.
* Every SQL statement uses asyncpg's ``$1, $2`` placeholders.
* JSONB columns are round-tripped as Python dicts thanks to a custom
  type codec registered in ``_init_connection`` — asyncpg otherwise
  decodes JSONB to raw strings.
* Timestamps are TIMESTAMPTZ, so every naive datetime the caller
  hands us is coerced to UTC-aware before binding (``_as_aware``).
  Return values are already aware because Postgres attaches the
  session TZ; we force UTC on the way out for parity with SQLite.
* Idempotency: schema DDL uses ``IF NOT EXISTS``; integrity errors
  on duplicate emails are wrapped into ``ValueError`` because callers
  already handle that in the SQLite impl.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final

import asyncpg

from capelle_platform.models.message import ChatMessage, MessageRole, MessageStatus
from capelle_platform.models.mode import DEFAULT_MODE
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
from capelle_platform.observability import get_logger
from capelle_platform.settings import Settings
from capelle_platform.store.base_store import BaseStore
from capelle_platform.utils import generate_id
from capelle_platform.web.host import DEFAULT_DOMAIN, Domain

_logger = get_logger(__name__)

# Location of the schema bootstrap script, co-located with this module
# so the file ships inside the installed package.
_SCHEMA_FILE: Final[Path] = Path(__file__).with_name("schema.sql")

# Fork-token TTL mirrors the SQLite impl so the two backends agree on
# how long a share link stays valid.
_FORK_TOKEN_TTL_DAYS: Final[int] = 30

# Connection-pool sizing. Values match the task spec and are deliberately
# conservative — most requests spend their time in the model, not the DB.
_POOL_MIN_SIZE: Final[int] = 2
_POOL_MAX_SIZE: Final[int] = 10


class PostgresStore(BaseStore):
    """
    Async Postgres store mirroring :class:`SQLiteStore`'s behaviour.

    Instantiated by ``AppBuilder`` when ``settings.store == 'postgres'``.
    The concrete DSN comes from ``settings.postgres_dsn``. The store
    owns the asyncpg pool and the schema bootstrap; no handler touches
    SQL directly.
    """

    def __init__(self, settings: Settings) -> None:
        """
        Record the DSN. Defer pool creation to :meth:`initialize`.

        We keep the constructor cheap so the Builder can wire the store
        before the event loop is running — exactly the same contract
        as the SQLite impl.
        """
        self._dsn: str = settings.postgres_dsn
        self._pool: asyncpg.Pool | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """
        Create the connection pool and bootstrap the schema.

        Registers a JSONB codec on every new connection so columns like
        ``chat_messages.metadata`` round-trip as plain Python dicts.
        Reads ``schema.sql`` from disk once at startup and executes it
        inside a single acquired connection; every statement is
        ``IF NOT EXISTS``, so re-running on an existing database is a
        no-op.
        """
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=_POOL_MIN_SIZE,
            max_size=_POOL_MAX_SIZE,
            init=self._init_connection,
        )
        schema_sql = _SCHEMA_FILE.read_text(encoding="utf-8")
        async with self._pool.acquire() as conn:
            await conn.execute(schema_sql)
        _logger.info("postgres_initialized", dsn=self._redact_dsn(self._dsn))

    async def close(self) -> None:
        """Drain the pool and release every idle connection."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @staticmethod
    async def _init_connection(conn: asyncpg.Connection) -> None:
        """
        Per-connection setup hook asyncpg calls on every new pool member.

        Registers a codec that encodes Python objects to JSON strings
        before they hit a JSONB column and decodes JSONB back into
        Python dicts/lists on read. Without this, asyncpg returns raw
        ``str`` for JSONB, which would force every call site to
        ``json.loads`` its own rows.
        """
        await conn.set_type_codec(
            "jsonb",
            encoder=lambda value: json.dumps(value, default=str),
            decoder=json.loads,
            schema="pg_catalog",
        )

    @staticmethod
    def _redact_dsn(dsn: str) -> str:
        """
        Mask the password portion of a DSN for safe logging.

        asyncpg accepts ``postgres://user:pass@host/db``; we chop the
        password so info-level logs never leak credentials to stdout.
        """
        if "@" not in dsn or "://" not in dsn:
            return dsn
        prefix, rest = dsn.split("://", 1)
        creds, tail = rest.split("@", 1)
        if ":" in creds:
            user, _ = creds.split(":", 1)
            return f"{prefix}://{user}:***@{tail}"
        return dsn

    def _ensure_pool(self) -> asyncpg.Pool:
        """Guard against use before :meth:`initialize`."""
        if self._pool is None:
            raise RuntimeError(
                "PostgresStore not initialized — call initialize() first"
            )
        return self._pool

    @staticmethod
    def _as_aware(dt: datetime) -> datetime:
        """
        Coerce a possibly-naive datetime to a UTC-aware datetime.

        Postgres ``TIMESTAMPTZ`` demands timezone info on the binding
        side; our domain models sometimes carry naive UTC timestamps
        from legacy code paths, so we attach ``timezone.utc`` when
        missing rather than failing. Already-aware datetimes pass
        through untouched.
        """
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    async def create_user(self, user: User) -> User:
        """
        Insert a new user row.

        ``daily_analysis_limit`` is persisted as-is; ``None`` means
        "no override — fall back to the global default" at quota-check
        time. Duplicate-email collisions raise ``UniqueViolationError``
        from asyncpg; we translate to ``ValueError`` so callers keep the
        same exception contract as the SQLite path.
        """
        pool = self._ensure_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO users "
                    "(id, email, hashed_password, created_at, daily_analysis_limit, "
                    "entra_oid, entra_tid, auth_source, email_verified) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                    user.id,
                    user.email,
                    user.hashed_password,
                    self._as_aware(user.created_at),
                    user.daily_analysis_limit,
                    user.entra_oid,
                    user.entra_tid,
                    user.auth_source,
                    user.email_verified,
                )
        except asyncpg.UniqueViolationError as exc:
            raise ValueError(f"Email already registered: {user.email}") from exc
        _logger.info("user_created", user_id=user.id, email=user.email)
        return user

    async def get_user_by_email(self, email: str) -> User | None:
        """Look up a user by email; return None on miss."""
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, email, hashed_password, created_at, daily_analysis_limit, "
                "entra_oid, entra_tid, auth_source, email_verified "
                "FROM users WHERE email = $1",
                email,
            )
        if row is None:
            return None
        return self._row_to_user(row)

    async def get_user_by_id(self, user_id: str) -> User | None:
        """Look up a user by ID; return None on miss."""
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, email, hashed_password, created_at, daily_analysis_limit, "
                "entra_oid, entra_tid, auth_source, email_verified "
                "FROM users WHERE id = $1",
                user_id,
            )
        if row is None:
            return None
        return self._row_to_user(row)

    async def get_user_by_entra(self, oid: str, tid: str) -> User | None:
        """
        Look up an SSO account by its Entra ``(oid, tid)`` identity pair.

        Both columns must match; mirrors the SQLite impl. Returns ``None``
        for a first-time SSO sign-in so the OIDC handler falls through to
        email linking / auto-provisioning.
        """
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, email, hashed_password, created_at, daily_analysis_limit, "
                "entra_oid, entra_tid, auth_source, email_verified "
                "FROM users WHERE entra_oid = $1 AND entra_tid = $2",
                oid,
                tid,
            )
        if row is None:
            return None
        return self._row_to_user(row)

    async def link_entra_identity(
        self, user_id: str, oid: str, tid: str
    ) -> None:
        """
        Attach an Entra ``(oid, tid)`` identity to an existing account.

        Persists the durable oid/tid key and flips ``auth_source`` to
        ``'sso'``. Mirrors the SQLite impl; idempotent.
        """
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET entra_oid = $1, entra_tid = $2, "
                "auth_source = 'sso' WHERE id = $3",
                oid,
                tid,
                user_id,
            )
        _logger.info("user_entra_linked", user_id=user_id)

    async def set_email_verified(self, user_id: str) -> None:
        """
        Flip ``email_verified`` to TRUE for a user. Idempotent — confirming an
        already-verified account simply re-sets TRUE.
        """
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET email_verified = TRUE WHERE id = $1",
                user_id,
            )
        _logger.info("user_email_verified", user_id=user_id)

    async def set_user_daily_limit(
        self, user_id: str, limit: int | None
    ) -> None:
        """
        Write the per-user daily-analysis override.

        Idempotent from the caller's perspective: repeated calls with
        the same ``(user_id, limit)`` never surface a different
        observable state. ``limit`` semantics are owned by the quota
        enforcer — ``None`` clears the override, ``0`` grants
        unlimited, positive integers cap the user. The admin endpoint
        validates the value before we get here.
        """
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET daily_analysis_limit = $1 WHERE id = $2",
                limit,
                user_id,
            )
        _logger.info(
            "user_daily_limit_set", user_id=user_id, limit=limit
        )

    @staticmethod
    def _row_to_user(row: asyncpg.Record) -> User:
        """
        Translate a users row into the domain model.

        ``daily_analysis_limit`` is nullable; ``None`` means the row has
        no override and the quota enforcer will use the global default.
        """
        limit_raw = row["daily_analysis_limit"]
        return User(
            id=row["id"],
            email=row["email"],
            hashed_password=row["hashed_password"],
            created_at=row["created_at"],
            daily_analysis_limit=(
                int(limit_raw) if limit_raw is not None else None
            ),
            entra_oid=row["entra_oid"],
            entra_tid=row["entra_tid"],
            auth_source=row["auth_source"] or "password",
            email_verified=bool(row["email_verified"]),
        )

    # ------------------------------------------------------------------
    # Chat sessions
    # ------------------------------------------------------------------

    async def create_session(self, session: ChatSession) -> ChatSession:
        """Insert a new chat session and return the supplied model."""
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO chat_sessions "
                "(id, user_id, title, topic, mode, domain, "
                "created_at, updated_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                session.id,
                session.user_id,
                session.title,
                session.topic,
                session.mode,
                session.domain,
                self._as_aware(session.created_at),
                self._as_aware(session.updated_at),
            )
        _logger.info(
            "session_created",
            session_id=session.id,
            mode=session.mode,
            domain=session.domain,
        )
        return session

    async def get_session(self, session_id: str) -> ChatSession | None:
        """
        Fetch a session by ID, including soft-deleted rows.

        Handlers decide whether to surface a tombstoned session — fork
        view still resolves them, chat flow rejects them. Matches the
        SQLite impl exactly.
        """
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, user_id, title, topic, mode, domain, "
                "created_at, updated_at, deleted_at "
                "FROM chat_sessions WHERE id = $1",
                session_id,
            )
        if row is None:
            return None
        return self._row_to_session(row)

    async def list_sessions(
        self, user_id: str, *, domain: Domain | None = None
    ) -> list[ChatSession]:
        """
        Return non-deleted sessions for ``user_id``, newest first.

        When ``domain`` is supplied, filter by the session namespace.
        """
        pool = self._ensure_pool()
        sql = (
            "SELECT id, user_id, title, topic, mode, domain, "
            "created_at, updated_at, deleted_at "
            "FROM chat_sessions "
            "WHERE user_id = $1 AND deleted_at IS NULL "
        )
        params: list[str] = [user_id]
        if domain is not None:
            params.append(domain)
            sql += f"AND domain = ${len(params)} "
        sql += "ORDER BY updated_at DESC"
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [self._row_to_session(r) for r in rows]

    async def update_session_title(self, session_id: str, title: str) -> None:
        """Rename a session and bump ``updated_at`` to now."""
        pool = self._ensure_pool()
        now = datetime.now(timezone.utc)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE chat_sessions SET title = $1, updated_at = $2 WHERE id = $3",
                title,
                now,
                session_id,
            )

    async def update_session_topic(self, session_id: str, topic: str) -> None:
        """
        Persist a topic tag without re-floating the session.

        Intentionally skips ``updated_at`` so tagging does not disturb
        the Recent ordering — identical rationale to the SQLite impl.
        """
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE chat_sessions SET topic = $1 WHERE id = $2",
                topic,
                session_id,
            )
        _logger.info("session_topic_updated", session_id=session_id, topic=topic)

    async def soft_delete_session(self, session_id: str) -> None:
        """
        Mark a session deleted if it is not already deleted.

        The ``deleted_at IS NULL`` guard preserves idempotency: a second
        delete never overwrites the original tombstone timestamp.
        """
        pool = self._ensure_pool()
        now = datetime.now(timezone.utc)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE chat_sessions SET deleted_at = $1 "
                "WHERE id = $2 AND deleted_at IS NULL",
                now,
                session_id,
            )
        _logger.info("session_soft_deleted", session_id=session_id)

    @staticmethod
    def _row_to_session(row: asyncpg.Record) -> ChatSession:
        """
        Translate a session row into the domain model.

        TIMESTAMPTZ comes back as aware datetimes; we do not re-wrap
        them. ``topic`` and ``deleted_at`` are nullable so we pass them
        through directly. ``mode`` and ``domain`` are non-null at the DDL
        level — the column defaults and the idempotent ALTERs carry older
        rows forward without backfill work here.
        """
        return ChatSession(
            id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            topic=row["topic"],
            mode=row["mode"] or DEFAULT_MODE,
            domain=row["domain"] or DEFAULT_DOMAIN,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            deleted_at=row["deleted_at"],
        )

    # ------------------------------------------------------------------
    # Chat messages
    # ------------------------------------------------------------------

    async def create_message(self, message: ChatMessage) -> ChatMessage:
        """
        Insert a message and bounce the parent session's updated_at.

        Both statements run inside a single ``conn.transaction()`` so the
        insert and the ordering bump commit (or roll back) together
        (STORE-4). The previous "for parity" un-transacted form could
        leave an orphaned message if the second statement failed.
        """
        pool = self._ensure_pool()
        metadata_payload: dict[str, Any] | None = (
            dict(message.metadata) if message.metadata else None
        )
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO chat_messages "
                    "(id, session_id, role, content, status, metadata, created_at) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                    message.id,
                    message.session_id,
                    message.role.value,
                    message.content,
                    message.status.value,
                    metadata_payload,
                    self._as_aware(message.created_at),
                )
                await conn.execute(
                    "UPDATE chat_sessions SET updated_at = $1 WHERE id = $2",
                    datetime.now(timezone.utc),
                    message.session_id,
                )
        return message

    async def get_message(self, message_id: str) -> ChatMessage | None:
        """Fetch a single message by ID; return None on miss."""
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, session_id, role, content, status, metadata, created_at "
                "FROM chat_messages WHERE id = $1",
                message_id,
            )
        if row is None:
            return None
        return self._row_to_message(row)

    async def list_messages(self, session_id: str) -> list[ChatMessage]:
        """Return every message in ``session_id``, oldest first."""
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, session_id, role, content, status, metadata, created_at "
                "FROM chat_messages WHERE session_id = $1 "
                "ORDER BY created_at ASC",
                session_id,
            )
        return [self._row_to_message(r) for r in rows]

    async def update_message(
        self,
        message_id: str,
        content: str | None = None,
        status: MessageStatus | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """
        Partial update for content / status / metadata.

        Short-circuits when all three arguments are None — callers
        frequently pass ``metadata=None`` just to avoid touching it,
        and a zero-field UPDATE would error out.
        """
        pool = self._ensure_pool()
        updates: list[str] = []
        params: list[Any] = []
        idx = 1
        if content is not None:
            updates.append(f"content = ${idx}")
            params.append(content)
            idx += 1
        if status is not None:
            updates.append(f"status = ${idx}")
            params.append(status.value)
            idx += 1
        if metadata is not None:
            updates.append(f"metadata = ${idx}")
            params.append(dict(metadata))
            idx += 1
        if not updates:
            return
        params.append(message_id)
        sql = f"UPDATE chat_messages SET {', '.join(updates)} WHERE id = ${idx}"
        async with pool.acquire() as conn:
            await conn.execute(sql, *params)

    async def claim_message_for_processing(
        self,
        message_id: str,
        *,
        expected_status: MessageStatus,
        claimed_status: MessageStatus,
    ) -> bool:
        """
        Conditional claim via a guarded UPDATE; ``True`` iff we won.

        asyncpg's ``execute`` returns a status string like ``"UPDATE 1"``;
        we parse the affected-row count off the end. A redelivered job
        finds the message already past ``expected_status`` (``"UPDATE 0"``)
        and aborts.
        """
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            status = await conn.execute(
                "UPDATE chat_messages SET status = $1 "
                "WHERE id = $2 AND status = $3",
                claimed_status.value,
                message_id,
                expected_status.value,
            )
        won = status.rsplit(" ", 1)[-1] == "1"
        if not won:
            _logger.info(
                "message_claim_skipped",
                message_id=message_id,
                expected=expected_status.value,
            )
        return won

    async def fail_stale_messages(
        self,
        *,
        non_terminal: tuple[MessageStatus, ...],
        older_than: datetime,
        failure_content: str,
    ) -> list[tuple[str, str]]:
        """
        Sweep aged non-terminal messages into FAILED via a CTE + RETURNING.

        A single atomic statement: the CTE flips the rows and returns
        their ids, which we join back to ``chat_sessions`` to surface the
        owning ``user_id`` so the worker can broadcast ``message_failed``
        to the affected sockets.
        """
        pool = self._ensure_pool()
        if not non_terminal:
            return []
        statuses = [s.value for s in non_terminal]
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "WITH reaped AS ("
                "  UPDATE chat_messages SET status = $1, content = $2 "
                "  WHERE status = ANY($3::text[]) AND created_at < $4 "
                "  RETURNING id, session_id"
                ") "
                "SELECT r.id AS id, s.user_id AS user_id "
                "FROM reaped r JOIN chat_sessions s ON s.id = r.session_id",
                MessageStatus.FAILED.value,
                failure_content,
                statuses,
                self._as_aware(older_than),
            )
        stale = [(row["id"], row["user_id"]) for row in rows]
        if stale:
            _logger.info("stale_messages_failed", count=len(stale))
        return stale

    # --- Daily quota counter (atomic reservation ledger) ---

    async def reserve_daily_quota(
        self, user_id: str, day: str, limit: int
    ) -> bool:
        """
        Atomic reserve-if-below-limit via INSERT ... ON CONFLICT ... RETURNING.

        The conditional ``WHERE daily_quota_counters.n < $3`` on the
        conflict branch means an at-cap row produces no returned row, so
        the presence of a RETURNING row IS the win/lose signal — no extra
        read, no race.
        """
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO daily_quota_counters (user_id, day, n) "
                "VALUES ($1, $2, 1) "
                "ON CONFLICT (user_id, day) DO UPDATE SET n = daily_quota_counters.n + 1 "
                "WHERE daily_quota_counters.n < $3 "
                "RETURNING n",
                user_id,
                day,
                limit,
            )
        reserved = row is not None
        if not reserved:
            _logger.info(
                "daily_quota_denied", user_id=user_id, day=day, limit=limit
            )
        return reserved

    async def release_daily_quota(self, user_id: str, day: str) -> None:
        """Give one reserved slot back, clamped at zero via GREATEST."""
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE daily_quota_counters SET n = GREATEST(0, n - 1) "
                "WHERE user_id = $1 AND day = $2",
                user_id,
                day,
            )

    async def get_daily_quota_used(self, user_id: str, day: str) -> int:
        """Return the reserved count for the day, or 0 when absent."""
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT n FROM daily_quota_counters "
                "WHERE user_id = $1 AND day = $2",
                user_id,
                day,
            )
        return int(value or 0)

    async def search_messages(
        self, user_id: str, query: str, limit: int = 50
    ) -> list[ChatMessage]:
        """
        Case-insensitive substring search scoped to ``user_id``'s sessions.

        Uses Postgres ``ILIKE`` — native case-insensitive LIKE — and
        joins through ``chat_sessions`` to enforce tenant isolation
        in SQL, never trusting handlers to filter correctly.
        """
        pool = self._ensure_pool()
        pattern = f"%{query}%"
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT m.id, m.session_id, m.role, m.content, m.status,
                       m.metadata, m.created_at
                FROM chat_messages m
                INNER JOIN chat_sessions s ON s.id = m.session_id
                WHERE s.user_id = $1
                  AND m.content ILIKE $2
                ORDER BY m.created_at DESC
                LIMIT $3
                """,
                user_id,
                pattern,
                int(limit),
            )
        return [self._row_to_message(r) for r in rows]

    @staticmethod
    def _row_to_message(row: asyncpg.Record) -> ChatMessage:
        """
        Build a ChatMessage from a fetched row.

        ``metadata`` is already a dict (or None) thanks to the JSONB
        codec registered in :meth:`_init_connection`.
        """
        meta = row["metadata"]
        return ChatMessage(
            id=row["id"],
            session_id=row["session_id"],
            role=MessageRole(row["role"]),
            content=row["content"],
            status=MessageStatus(row["status"]),
            metadata=meta if isinstance(meta, dict) else None,
            created_at=row["created_at"],
        )

    # ------------------------------------------------------------------
    # Fork links
    # ------------------------------------------------------------------

    async def create_fork_token(self, session_id: str) -> tuple[str, str]:
        """
        Mint a 30-day urlsafe token and persist it.

        Returns ``(token, expires_at_iso)``; the ISO form is what the
        REST layer serialises, so we produce it here to keep the
        caller ignorant of timezone wiring.
        """
        pool = self._ensure_pool()
        token = secrets.token_urlsafe(24)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=_FORK_TOKEN_TTL_DAYS)
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO fork_tokens (token, session_id, created_at, expires_at) "
                "VALUES ($1, $2, $3, $4)",
                token,
                session_id,
                now,
                expires,
            )
        _logger.info(
            "fork_token_created",
            session_id=session_id,
            expires_at=expires.isoformat(),
        )
        return token, expires.isoformat()

    async def resolve_fork_token(self, token: str) -> ChatSession | None:
        """
        Look up a fork token and return its session if unexpired.

        Expiration is checked in Python (identical to SQLite) so a
        skewed server clock never silently leaks stale links.
        """
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT session_id, expires_at FROM fork_tokens WHERE token = $1",
                token,
            )
        if row is None:
            return None
        expires = row["expires_at"]
        if expires < datetime.now(timezone.utc):
            _logger.info("fork_token_expired", token_prefix=token[:8])
            return None
        return await self.get_session(row["session_id"])

    async def clone_session_for_user(
        self, source_session_id: str, new_user_id: str
    ) -> ChatSession:
        """
        Deep-copy a session (metadata + chronologically-ordered messages).

        Re-issues fresh IDs for the session and every message, leaving
        the source untouched. Title gains a "(fork)" suffix so the UI
        shows both copies in the recipient's session list.
        """
        pool = self._ensure_pool()
        source = await self.get_session(source_session_id)
        if source is None:
            raise ValueError(f"Source session not found: {source_session_id}")

        now = datetime.now(timezone.utc)
        new_session = ChatSession(
            id=generate_id(),
            user_id=new_user_id,
            title=f"{source.title} (fork)",
            topic=source.topic,
            mode=source.mode,
            domain=source.domain,
            created_at=now,
            updated_at=now,
        )
        source_messages = await self.list_messages(source_session_id)

        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO chat_sessions "
                "(id, user_id, title, topic, mode, domain, "
                "created_at, updated_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                new_session.id,
                new_session.user_id,
                new_session.title,
                new_session.topic,
                new_session.mode,
                new_session.domain,
                new_session.created_at,
                new_session.updated_at,
            )
            # Bulk-insert messages preserving chronological order. We
            # reuse executemany so the network roundtrip cost stays
            # flat regardless of how many messages the source has.
            if source_messages:
                await conn.executemany(
                    "INSERT INTO chat_messages "
                    "(id, session_id, role, content, status, metadata, created_at) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                    [
                        (
                            generate_id(),
                            new_session.id,
                            src.role.value,
                            src.content,
                            src.status.value,
                            dict(src.metadata) if src.metadata else None,
                            self._as_aware(src.created_at),
                        )
                        for src in source_messages
                    ],
                )
        _logger.info(
            "session_cloned",
            source=source_session_id,
            target=new_session.id,
            user_id=new_user_id,
            message_count=len(source_messages),
        )
        return new_session

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------

    async def insert_usage_event(self, event: UsageEvent) -> None:
        """
        Fire-and-forget telemetry insert.

        Swallows ``asyncpg.PostgresError`` by design: telemetry sits
        on the hot path (every handler records events), so a transient
        DB blip must never propagate into a user-visible 500.
        """
        pool = self._ensure_pool()
        props_payload: dict[str, Any] | None = (
            dict(event.properties) if event.properties else None
        )
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO usage_events "
                    "(id, user_id, session_id, event_type, properties, ts) "
                    "VALUES ($1, $2, $3, $4, $5, $6)",
                    event.id,
                    event.user_id,
                    event.session_id,
                    event.event_type,
                    props_payload,
                    self._as_aware(event.ts),
                )
        except asyncpg.PostgresError as exc:
            _logger.warning(
                "usage_event_insert_failed",
                event_type=event.event_type,
                error=str(exc),
            )

    async def count_events_since(
        self,
        user_id: str,
        event_type: TelemetryEventType,
        since: datetime,
    ) -> int:
        """Count matching events with ``ts >= since`` using native TIMESTAMPTZ."""
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT COUNT(*) FROM usage_events "
                "WHERE user_id = $1 AND event_type = $2 AND ts >= $3",
                user_id,
                event_type,
                self._as_aware(since),
            )
        return int(value or 0)

    async def list_usage_events(
        self,
        *,
        event_type: TelemetryEventType | None = None,
        user_id: str | None = None,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[UsageEvent]:
        """
        Read telemetry rows with optional filters, newest first.

        Limit is clamped to 5000 to match the SQLite impl — protects
        the admin dashboard against an accidental full-table pull.
        """
        pool = self._ensure_pool()
        effective_limit = max(1, min(int(limit), 5000))

        clauses: list[str] = []
        params: list[Any] = []
        idx = 1
        if event_type is not None:
            clauses.append(f"event_type = ${idx}")
            params.append(event_type)
            idx += 1
        if user_id is not None:
            clauses.append(f"user_id = ${idx}")
            params.append(user_id)
            idx += 1
        if since is not None:
            clauses.append(f"ts >= ${idx}")
            params.append(self._as_aware(since))
            idx += 1
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT id, user_id, session_id, event_type, properties, ts "
            f"FROM usage_events {where} ORDER BY ts DESC LIMIT ${idx}"
        )
        params.append(effective_limit)
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [self._row_to_usage_event(r) for r in rows]

    async def count_users_created_since(self, since: datetime) -> int:
        """Count user rows whose ``created_at >= since``."""
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE created_at >= $1",
                self._as_aware(since),
            )
        return int(value or 0)

    async def aggregate_engagement_since(
        self,
        since: datetime,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> list[UserEngagement]:
        """
        Per-user engagement rollup scoped to the trailing window.

        Uses ``COUNT(CASE WHEN ...)`` conditional aggregation so every
        tracked event type is summed in a single pass. Ordering by
        ``last_active DESC`` matches the SQLite impl exactly.
        """
        pool = self._ensure_pool()
        effective_limit = max(1, min(int(limit), 2000))
        effective_offset = max(0, int(offset))
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    e.user_id AS user_id,
                    u.email AS email,
                    u.created_at AS first_seen,
                    COUNT(CASE WHEN e.event_type='session_created' THEN 1 END) AS sessions_created,
                    COUNT(CASE WHEN e.event_type='message_sent' THEN 1 END) AS messages_sent,
                    COUNT(CASE WHEN e.event_type='analysis_completed' THEN 1 END) AS analyses_completed,
                    COUNT(CASE WHEN e.event_type='analysis_failed' THEN 1 END) AS analyses_failed,
                    COUNT(CASE WHEN e.event_type='share_link_minted' THEN 1 END) AS share_links_minted,
                    COUNT(CASE WHEN e.event_type='share_link_opened' THEN 1 END) AS share_links_opened,
                    COUNT(CASE WHEN e.event_type='feedback_submitted' THEN 1 END) AS feedback_submitted,
                    COUNT(CASE WHEN e.event_type='quota_limit_hit' THEN 1 END) AS quota_limit_hit,
                    MAX(e.ts) AS last_active
                FROM usage_events e
                JOIN users u ON u.id = e.user_id
                WHERE e.ts >= $1
                GROUP BY e.user_id, u.email, u.created_at
                ORDER BY last_active DESC
                LIMIT $2 OFFSET $3
                """,
                self._as_aware(since),
                effective_limit,
                effective_offset,
            )
        return [
            UserEngagement(
                user_id=row["user_id"],
                email=row["email"],
                sessions_created=int(row["sessions_created"] or 0),
                messages_sent=int(row["messages_sent"] or 0),
                analyses_completed=int(row["analyses_completed"] or 0),
                analyses_failed=int(row["analyses_failed"] or 0),
                share_links_minted=int(row["share_links_minted"] or 0),
                share_links_opened=int(row["share_links_opened"] or 0),
                feedback_submitted=int(row["feedback_submitted"] or 0),
                quota_limit_hit=int(row["quota_limit_hit"] or 0),
                first_seen=row["first_seen"],
                last_active=row["last_active"],
            )
            for row in rows
        ]

    async def list_recent_questions(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> list[UserQuestion]:
        """
        User-role messages joined on session + user, newest first.

        Filters out messages whose session is soft-deleted
        (``chat_sessions.deleted_at IS NOT NULL``) so admins only see
        active content.
        """
        pool = self._ensure_pool()
        effective_limit = max(1, min(int(limit), 1000))
        effective_offset = max(0, int(offset))
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    m.id          AS message_id,
                    m.session_id  AS session_id,
                    s.title       AS session_title,
                    s.topic       AS session_topic,
                    u.id          AS user_id,
                    u.email       AS user_email,
                    m.content     AS content,
                    m.created_at  AS created_at
                FROM chat_messages m
                JOIN chat_sessions s ON s.id = m.session_id
                JOIN users u         ON u.id = s.user_id
                WHERE m.role = 'user'
                  AND s.deleted_at IS NULL
                ORDER BY m.created_at DESC
                LIMIT $1 OFFSET $2
                """,
                effective_limit,
                effective_offset,
            )
        return [
            UserQuestion(
                message_id=row["message_id"],
                session_id=row["session_id"],
                session_title=row["session_title"],
                session_topic=row["session_topic"],
                user_id=row["user_id"],
                user_email=row["user_email"],
                content=row["content"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def list_users_paginated(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        admin_emails: frozenset[str] = frozenset(),
    ) -> list[UserRecord]:
        """
        Return every user, newest first, with activity rollups.

        LEFT JOIN on ``usage_events`` so dormant accounts still show
        up — the users page is the canonical "who has registered"
        view, distinct from the engagement page.
        """
        pool = self._ensure_pool()
        effective_limit = max(1, min(int(limit), 1000))
        effective_offset = max(0, int(offset))
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    u.id AS user_id,
                    u.email AS email,
                    u.created_at AS created_at,
                    u.daily_analysis_limit AS daily_analysis_limit,
                    COUNT(CASE WHEN e.event_type='session_created' THEN 1 END) AS sessions,
                    COUNT(CASE WHEN e.event_type='message_sent' THEN 1 END) AS messages_sent,
                    COUNT(CASE WHEN e.event_type='analysis_completed' THEN 1 END) AS analyses_completed,
                    MAX(e.ts) AS last_active
                FROM users u
                LEFT JOIN usage_events e ON e.user_id = u.id
                GROUP BY u.id, u.email, u.created_at, u.daily_analysis_limit
                ORDER BY u.created_at DESC
                LIMIT $1 OFFSET $2
                """,
                effective_limit,
                effective_offset,
            )
        return [
            UserRecord(
                user_id=row["user_id"],
                email=row["email"],
                created_at=row["created_at"],
                is_admin=row["email"].strip().lower() in admin_emails,
                sessions=int(row["sessions"] or 0),
                messages_sent=int(row["messages_sent"] or 0),
                analyses_completed=int(row["analyses_completed"] or 0),
                last_active=row["last_active"],
                daily_analysis_limit_override=(
                    int(row["daily_analysis_limit"])
                    if row["daily_analysis_limit"] is not None
                    else None
                ),
            )
            for row in rows
        ]

    async def sum_tokens_by_user_since(
        self,
        since: datetime,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> list[UserTokenTotals]:
        """
        Aggregate per-user token spend using JSONB property extraction.

        ``properties->>'key'`` returns the property as text; cast to
        ``bigint`` for the SUM (tokens comfortably fit, but some power
        users blow past INTEGER range over a month). ``COALESCE(..., 0)``
        tolerates pre-token-accounting rows that lack the keys.
        """
        pool = self._ensure_pool()
        effective_limit = max(1, min(int(limit), 1000))
        effective_offset = max(0, int(offset))
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    e.user_id AS user_id,
                    u.email AS email,
                    COUNT(*) AS analyses,
                    COALESCE(SUM(
                        (e.properties->>'input_tokens')::bigint
                    ), 0) AS input_tokens,
                    COALESCE(SUM(
                        (e.properties->>'output_tokens')::bigint
                    ), 0) AS output_tokens,
                    COALESCE(SUM(
                        (e.properties->>'cache_creation_input_tokens')::bigint
                    ), 0) AS cache_creation_input_tokens,
                    COALESCE(SUM(
                        (e.properties->>'cache_read_input_tokens')::bigint
                    ), 0) AS cache_read_input_tokens,
                    COALESCE(SUM(
                        (e.properties->>'total_tokens')::bigint
                    ), 0) AS total_tokens
                FROM usage_events e
                JOIN users u ON u.id = e.user_id
                WHERE e.event_type = 'analysis_completed'
                  AND e.ts >= $1
                GROUP BY e.user_id, u.email
                ORDER BY total_tokens DESC, analyses DESC
                LIMIT $2 OFFSET $3
                """,
                self._as_aware(since),
                effective_limit,
                effective_offset,
            )
        return [
            UserTokenTotals(
                user_id=row["user_id"],
                email=row["email"],
                analyses=int(row["analyses"]),
                input_tokens=int(row["input_tokens"] or 0),
                output_tokens=int(row["output_tokens"] or 0),
                cache_creation_input_tokens=int(
                    row["cache_creation_input_tokens"] or 0
                ),
                cache_read_input_tokens=int(
                    row["cache_read_input_tokens"] or 0
                ),
                total_tokens=int(row["total_tokens"] or 0),
            )
            for row in rows
        ]

    async def sum_tokens_grouped_by_day(
        self,
        *,
        days: int = 30,
    ) -> list[tuple[str, int]]:
        """
        Daily total-token series over the trailing ``days`` window.

        Gaps are zero-filled in Python so the UI's spark chart renders
        a continuous line even on low-traffic dev DBs. The ``to_char``
        call bucket-groups by UTC calendar day, mirroring SQLite's
        ``substr(ts, 1, 10)`` behaviour.
        """
        pool = self._ensure_pool()
        window = max(1, min(int(days), 365))
        since = datetime.now(timezone.utc) - timedelta(days=window - 1)
        since_midnight = since.replace(hour=0, minute=0, second=0, microsecond=0)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS day,
                    COALESCE(SUM(
                        (properties->>'total_tokens')::bigint
                    ), 0) AS total
                FROM usage_events
                WHERE event_type = 'analysis_completed' AND ts >= $1
                GROUP BY day
                ORDER BY day ASC
                """,
                since_midnight,
            )
        totals: dict[str, int] = {row["day"]: int(row["total"] or 0) for row in rows}
        series: list[tuple[str, int]] = []
        for offset in range(window):
            day = (since_midnight + timedelta(days=offset)).strftime("%Y-%m-%d")
            series.append((day, totals.get(day, 0)))
        return series

    async def sum_tokens_breakdown_grouped_by_day(
        self,
        *,
        days: int = 30,
    ) -> list[tuple[str, int, int, int]]:
        """
        Per-day ``(day, input, output, cache)`` totals over the trailing
        ``days`` window. ``cache`` collapses cache_creation_input_tokens
        + cache_read_input_tokens into one stacked-chart slice. Missing
        days zero-fill.
        """
        pool = self._ensure_pool()
        window = max(1, min(int(days), 365))
        since = datetime.now(timezone.utc) - timedelta(days=window - 1)
        since_midnight = since.replace(hour=0, minute=0, second=0, microsecond=0)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS day,
                    COALESCE(SUM(
                        (properties->>'input_tokens')::bigint
                    ), 0) AS input_tokens,
                    COALESCE(SUM(
                        (properties->>'output_tokens')::bigint
                    ), 0) AS output_tokens,
                    COALESCE(SUM(
                        (properties->>'cache_creation_input_tokens')::bigint
                      + (properties->>'cache_read_input_tokens')::bigint
                    ), 0) AS cache_tokens
                FROM usage_events
                WHERE event_type = 'analysis_completed' AND ts >= $1
                GROUP BY day
                ORDER BY day ASC
                """,
                since_midnight,
            )
        by_day: dict[str, tuple[int, int, int]] = {
            row["day"]: (
                int(row["input_tokens"] or 0),
                int(row["output_tokens"] or 0),
                int(row["cache_tokens"] or 0),
            )
            for row in rows
        }
        series: list[tuple[str, int, int, int]] = []
        for offset in range(window):
            day = (since_midnight + timedelta(days=offset)).strftime("%Y-%m-%d")
            inp, out, cache = by_day.get(day, (0, 0, 0))
            series.append((day, inp, out, cache))
        return series

    async def count_events_grouped_by_hour(
        self,
        event_type: TelemetryEventType,
        *,
        hours: int = 24,
    ) -> list[tuple[str, int]]:
        """Bucket matching events by UTC hour; zero-fill the window."""
        pool = self._ensure_pool()
        window = max(1, min(int(hours), 24 * 7))
        now = datetime.now(timezone.utc).replace(
            minute=0, second=0, microsecond=0
        )
        since = now - timedelta(hours=window - 1)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24') AS hour,
                       COUNT(*) AS n
                FROM usage_events
                WHERE event_type = $1 AND ts >= $2
                GROUP BY hour
                ORDER BY hour ASC
                """,
                event_type,
                since,
            )
        counts: dict[str, int] = {row["hour"]: int(row["n"]) for row in rows}
        result: list[tuple[str, int]] = []
        for offset in range(window):
            key = (since + timedelta(hours=offset)).strftime("%Y-%m-%dT%H")
            result.append((key, counts.get(key, 0)))
        return result

    async def count_events_grouped_by_day(
        self,
        event_type: TelemetryEventType,
        *,
        days: int = 30,
    ) -> list[tuple[str, int]]:
        """
        Per-day event-count series over the trailing ``days`` window.

        Zero-fills missing days so the dashboard's time-series renders
        continuously — same contract as :meth:`sum_tokens_grouped_by_day`.
        """
        pool = self._ensure_pool()
        window = max(1, min(int(days), 365))
        since = datetime.now(timezone.utc) - timedelta(days=window - 1)
        since_midnight = since.replace(hour=0, minute=0, second=0, microsecond=0)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS day,
                    COUNT(*) AS n
                FROM usage_events
                WHERE event_type = $1 AND ts >= $2
                GROUP BY day
                ORDER BY day ASC
                """,
                event_type,
                since_midnight,
            )
        counts: dict[str, int] = {row["day"]: int(row["n"]) for row in rows}
        result: list[tuple[str, int]] = []
        for offset in range(window):
            day = (since_midnight + timedelta(days=offset)).strftime("%Y-%m-%d")
            result.append((day, counts.get(day, 0)))
        return result

    @staticmethod
    def _row_to_usage_event(row: asyncpg.Record) -> UsageEvent:
        """Build a UsageEvent domain object from a fetched row."""
        props = row["properties"]
        return UsageEvent(
            id=row["id"],
            user_id=row["user_id"],
            session_id=row["session_id"],
            event_type=row["event_type"],
            properties=props if isinstance(props, dict) else {},
            ts=row["ts"],
        )

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    async def insert_feedback(self, entry: FeedbackEntry) -> None:
        """Persist one feedback submission."""
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO feedback_entries "
                "(id, user_id, email, topic, content, status, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                entry.id,
                entry.user_id,
                entry.email,
                entry.topic,
                entry.content,
                entry.status,
                self._as_aware(entry.created_at),
            )
        _logger.info(
            "feedback_inserted",
            feedback_id=entry.id,
            topic=entry.topic,
            user_id=entry.user_id,
        )

    async def list_feedback(
        self,
        *,
        status: FeedbackStatus | None = None,
        topic: FeedbackTopic | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[FeedbackEntry]:
        """Return feedback rows newest first, honouring optional filters."""
        pool = self._ensure_pool()
        effective_limit = max(1, min(int(limit), 1000))
        effective_offset = max(0, int(offset))
        clauses: list[str] = []
        params: list[Any] = []
        idx = 1
        if status is not None:
            clauses.append(f"status = ${idx}")
            params.append(status)
            idx += 1
        if topic is not None:
            clauses.append(f"topic = ${idx}")
            params.append(topic)
            idx += 1
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT id, user_id, email, topic, content, status, created_at "
            f"FROM feedback_entries {where} "
            f"ORDER BY created_at DESC LIMIT ${idx} OFFSET ${idx + 1}"
        )
        params.append(effective_limit)
        params.append(effective_offset)
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [
            FeedbackEntry(
                id=row["id"],
                user_id=row["user_id"],
                email=row["email"],
                topic=row["topic"],
                content=row["content"],
                status=row["status"],
                created_at=row["created_at"],
            )
            for row in rows
        ]
