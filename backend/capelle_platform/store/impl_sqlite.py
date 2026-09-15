"""
SQLite persistence implementation using aiosqlite.

Lightweight, zero-dependency store for the MVP.  Swappable for Supabase
(asyncpg) by implementing the same BaseStore ABC.  All SQL is contained
in this single file — the rest of the platform never sees raw queries.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

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

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id                   TEXT PRIMARY KEY,
    email                TEXT UNIQUE NOT NULL,
    hashed_password      TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    -- Per-user daily-analysis override. NULL = fall back to the
    -- CAPELLE_DAILY_ANALYSIS_LIMIT default; 0 = unlimited; positive
    -- = user-specific cap. Older DBs are migrated in _migrate().
    daily_analysis_limit INTEGER,
    -- Entra/Azure AD OIDC SSO linkage. entra_oid + entra_tid form the
    -- durable identity key for SSO accounts (NULL for password users);
    -- auth_source is 'password' or 'sso'. Older DBs are migrated in
    -- _migrate() (columns added, legacy rows defaulted to 'password').
    entra_oid            TEXT,
    entra_tid            TEXT,
    auth_source          TEXT NOT NULL DEFAULT 'password'
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    title       TEXT NOT NULL,
    topic       TEXT,
    -- Analysis mode chosen at session creation. Older rows are migrated
    -- to 'groeikern' (the legacy default) in _migrate().
    mode        TEXT NOT NULL DEFAULT 'groeikern',
    -- Session namespace. Older rows receive the 'capelle' default in _migrate().
    domain      TEXT NOT NULL DEFAULT 'capelle',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'complete',
    metadata    TEXT,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
);

CREATE TABLE IF NOT EXISTS fork_tokens (
    token       TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
);

CREATE TABLE IF NOT EXISTS usage_events (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    session_id  TEXT,
    event_type  TEXT NOT NULL,
    properties  TEXT,
    ts          TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_usage_events_user_ts ON usage_events(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_usage_events_type_ts ON usage_events(event_type, ts);
CREATE INDEX IF NOT EXISTS idx_usage_events_user_type_ts ON usage_events(user_id, event_type, ts);
CREATE INDEX IF NOT EXISTS idx_usage_events_ts ON usage_events(ts);

CREATE TABLE IF NOT EXISTS feedback_entries (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    email       TEXT NOT NULL,
    topic       TEXT NOT NULL,
    content     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'new',
    created_at  TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_feedback_status_created ON feedback_entries(status, created_at);

-- Atomic per-(user, day) daily-quota ledger. The composite primary key
-- gives us a single-row UPSERT target; ``n`` is the number of analyses
-- reserved for that local-calendar day. This is the durable quota gate
-- (replacing the TOCTOU count of analysis_completed events) — reserved
-- at check time, released on terminal failure.
CREATE TABLE IF NOT EXISTS daily_quota_counters (
    user_id  TEXT NOT NULL,
    day      TEXT NOT NULL,
    n        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);

"""

# Fork links live 30 days by default. Long enough for a colleague to
# come back to the link, short enough that old shared analyses don't
# leak forever.
_FORK_TOKEN_TTL_DAYS = 30


class SQLiteStore(BaseStore):
    """
    Async SQLite store for users, chat sessions, and messages.

    Uses aiosqlite for non-blocking IO.  The database file path comes
    from Settings so it can be overridden per environment.
    """

    def __init__(self, settings: Settings) -> None:
        """
        Construct the store with a path from application settings.

        The actual connection is created lazily in initialize().
        """
        self._db_path = str(settings.sqlite_path)
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """
        Open the database connection and create tables if they don't exist.

        Called once at startup by the Builder.
        """
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_CREATE_TABLES_SQL)
        await self._db.commit()
        await self._migrate()
        _logger.info("sqlite_initialized", path=self._db_path)

    async def _migrate(self) -> None:
        """
        Idempotent schema migrations.

        SQLite cannot conditionally add a column, so we probe the schema
        and only ALTER if the column is missing. Each migration is wrapped
        in a try/except so a partial upgrade never wedges startup.
        """
        assert self._db is not None
        # --- topic column on chat_sessions (Wave 2 B2) ---
        cursor = await self._db.execute("PRAGMA table_info(chat_sessions)")
        cols = {row["name"] for row in await cursor.fetchall()}
        if "topic" not in cols:
            try:
                await self._db.execute(
                    "ALTER TABLE chat_sessions ADD COLUMN topic TEXT"
                )
                await self._db.commit()
                _logger.info("sqlite_migrated", change="add_topic_column")
            except aiosqlite.OperationalError as exc:  # pragma: no cover
                # race: another process migrated first — safe to ignore
                _logger.info("sqlite_migrate_skip", reason=str(exc))

        # --- usage_events(ts) index for ts-range queries (engagement page) ---
        # The older composite indexes all lead with user_id or event_type;
        # the engagement aggregation filters only by ts and group-by user,
        # which forced a table scan until this index was added.
        try:
            await self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_events_ts ON usage_events(ts)"
            )
            await self._db.commit()
        except aiosqlite.OperationalError as exc:  # pragma: no cover
            _logger.info("sqlite_migrate_skip", reason=str(exc))

        # --- mode column on chat_sessions (per-session analysis mode) ---
        # Older DBs created before the mode field existed get the column
        # backfilled with 'groeikern' so existing sessions continue to
        # behave the way they were created. New rows always set it
        # explicitly via create_session.
        if "mode" not in cols:
            try:
                await self._db.execute(
                    "ALTER TABLE chat_sessions ADD COLUMN mode TEXT "
                    "NOT NULL DEFAULT 'groeikern'"
                )
                await self._db.commit()
                _logger.info("sqlite_migrated", change="add_mode_column")
            except aiosqlite.OperationalError as exc:  # pragma: no cover
                _logger.info("sqlite_migrate_skip", reason=str(exc))

        # --- session namespace column ---
        # Backfill existing rows with the default 'capelle' namespace.
        # New sessions set the namespace explicitly during creation.
        if "domain" not in cols:
            try:
                await self._db.execute(
                    "ALTER TABLE chat_sessions ADD COLUMN domain TEXT "
                    "NOT NULL DEFAULT 'capelle'"
                )
                await self._db.commit()
                _logger.info("sqlite_migrated", change="add_domain_column")
            except aiosqlite.OperationalError as exc:  # pragma: no cover
                _logger.info("sqlite_migrate_skip", reason=str(exc))

        # --- deleted_at column on chat_sessions (soft delete) ---
        # We never hard-delete sessions: they stay in the DB for audit
        # and future "restore" tooling. The frontend filters on this
        # column so users see the operation as destructive even though
        # the row persists.
        if "deleted_at" not in cols:
            try:
                await self._db.execute(
                    "ALTER TABLE chat_sessions ADD COLUMN deleted_at TEXT"
                )
                await self._db.commit()
                _logger.info("sqlite_migrated", change="add_deleted_at_column")
            except aiosqlite.OperationalError as exc:  # pragma: no cover
                _logger.info("sqlite_migrate_skip", reason=str(exc))

        # --- fork_tokens table (Wave 3 C2) ---
        # CREATE TABLE IF NOT EXISTS in the bootstrap script above already
        # handles fresh DBs; this is a defence-in-depth retry for older
        # databases created before the table existed.
        try:
            await self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS fork_tokens (
                    token       TEXT PRIMARY KEY,
                    session_id  TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    expires_at  TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
                )
                """
            )
            await self._db.commit()
        except aiosqlite.OperationalError as exc:  # pragma: no cover
            _logger.info("sqlite_migrate_skip", reason=str(exc))

        # --- daily_analysis_limit column on users (per-user quota override) ---
        # Re-probe users.table_info — the cursor above targeted chat_sessions
        # and its column set never includes this new field. Adding the column
        # as nullable INTEGER preserves every existing row at "NULL = use
        # global default", so a migration never changes behaviour for an
        # account that had no prior override.
        users_cursor = await self._db.execute("PRAGMA table_info(users)")
        users_cols = {row["name"] for row in await users_cursor.fetchall()}
        if "daily_analysis_limit" not in users_cols:
            try:
                await self._db.execute(
                    "ALTER TABLE users ADD COLUMN daily_analysis_limit INTEGER"
                )
                await self._db.commit()
                _logger.info(
                    "sqlite_migrated", change="add_daily_analysis_limit_column"
                )
            except aiosqlite.OperationalError as exc:  # pragma: no cover
                _logger.info("sqlite_migrate_skip", reason=str(exc))

        # --- Entra OIDC SSO columns on users (compass-entra-oidc-sso) ---
        # Older DBs predate Entra SSO; every existing account is a
        # password account, so the nullable oid/tid columns add as NULL and
        # auth_source backfills to 'password' (the column default applies to
        # every existing row at ALTER time). New SSO rows set these
        # explicitly via create_user. Adding each column behind its own
        # probe keeps the migration idempotent and partial-failure safe.
        if "entra_oid" not in users_cols:
            try:
                await self._db.execute(
                    "ALTER TABLE users ADD COLUMN entra_oid TEXT"
                )
                await self._db.commit()
                _logger.info("sqlite_migrated", change="add_entra_oid_column")
            except aiosqlite.OperationalError as exc:  # pragma: no cover
                _logger.info("sqlite_migrate_skip", reason=str(exc))
        if "entra_tid" not in users_cols:
            try:
                await self._db.execute(
                    "ALTER TABLE users ADD COLUMN entra_tid TEXT"
                )
                await self._db.commit()
                _logger.info("sqlite_migrated", change="add_entra_tid_column")
            except aiosqlite.OperationalError as exc:  # pragma: no cover
                _logger.info("sqlite_migrate_skip", reason=str(exc))
        if "auth_source" not in users_cols:
            try:
                await self._db.execute(
                    "ALTER TABLE users ADD COLUMN auth_source TEXT "
                    "NOT NULL DEFAULT 'password'"
                )
                await self._db.commit()
                _logger.info("sqlite_migrated", change="add_auth_source_column")
            except aiosqlite.OperationalError as exc:  # pragma: no cover
                _logger.info("sqlite_migrate_skip", reason=str(exc))

        # --- daily_quota_counters table (atomic quota ledger, STORE-2) ---
        # CREATE TABLE IF NOT EXISTS in the bootstrap script already covers
        # fresh DBs; this retry carries older databases forward without a
        # fresh boot.
        try:
            await self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_quota_counters (
                    user_id  TEXT NOT NULL,
                    day      TEXT NOT NULL,
                    n        INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, day)
                )
                """
            )
            await self._db.commit()
        except aiosqlite.OperationalError as exc:  # pragma: no cover
            _logger.info("sqlite_migrate_skip", reason=str(exc))

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    def _ensure_db(self) -> aiosqlite.Connection:
        """Guard against use before initialize()."""
        if self._db is None:
            raise RuntimeError("Store not initialized — call initialize() first")
        return self._db

    # --- Users ---

    async def create_user(self, user: User) -> User:
        """
        Insert a new user record.

        ``daily_analysis_limit`` is persisted as-is: the default is
        ``None`` which means "fall back to the global default" at
        quota-check time.  Raises ``ValueError`` on duplicate email.
        """
        db = self._ensure_db()
        try:
            await db.execute(
                "INSERT INTO users "
                "(id, email, hashed_password, created_at, daily_analysis_limit, "
                "entra_oid, entra_tid, auth_source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user.id,
                    user.email,
                    user.hashed_password,
                    user.created_at.isoformat(),
                    user.daily_analysis_limit,
                    user.entra_oid,
                    user.entra_tid,
                    user.auth_source,
                ),
            )
            await db.commit()
        except aiosqlite.IntegrityError as exc:
            raise ValueError(f"Email already registered: {user.email}") from exc
        _logger.info("user_created", user_id=user.id, email=user.email)
        return user

    async def get_user_by_email(self, email: str) -> User | None:
        """Look up a user by email address."""
        db = self._ensure_db()
        cursor = await db.execute(
            "SELECT id, email, hashed_password, created_at, daily_analysis_limit, "
            "entra_oid, entra_tid, auth_source "
            "FROM users WHERE email = ?",
            (email,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_user(row)

    async def get_user_by_id(self, user_id: str) -> User | None:
        """Look up a user by ID."""
        db = self._ensure_db()
        cursor = await db.execute(
            "SELECT id, email, hashed_password, created_at, daily_analysis_limit, "
            "entra_oid, entra_tid, auth_source "
            "FROM users WHERE id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_user(row)

    async def get_user_by_entra(self, oid: str, tid: str) -> User | None:
        """
        Look up an SSO account by its Entra ``(oid, tid)`` identity pair.

        Both columns must match; a partial match resolves no user. Returns
        ``None`` for a first-time SSO sign-in so the OIDC handler falls
        through to email linking / auto-provisioning.
        """
        db = self._ensure_db()
        cursor = await db.execute(
            "SELECT id, email, hashed_password, created_at, daily_analysis_limit, "
            "entra_oid, entra_tid, auth_source "
            "FROM users WHERE entra_oid = ? AND entra_tid = ?",
            (oid, tid),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_user(row)

    async def link_entra_identity(
        self, user_id: str, oid: str, tid: str
    ) -> None:
        """
        Attach an Entra ``(oid, tid)`` identity to an existing account.

        Called when an SSO sign-in matches a pre-existing user by email:
        we persist the durable oid/tid key and flip ``auth_source`` to
        ``'sso'`` so a later email change still re-links via
        :meth:`get_user_by_entra`. Idempotent — re-linking the same pair
        is a no-op from the caller's perspective.
        """
        db = self._ensure_db()
        await db.execute(
            "UPDATE users SET entra_oid = ?, entra_tid = ?, auth_source = 'sso' "
            "WHERE id = ?",
            (oid, tid, user_id),
        )
        await db.commit()
        _logger.info("user_entra_linked", user_id=user_id)

    async def set_user_daily_limit(
        self, user_id: str, limit: int | None
    ) -> None:
        """
        Write the per-user daily-analysis override.

        Idempotent: re-issuing the same ``(user_id, limit)`` is a no-op
        from the caller's perspective even though SQLite still executes
        the UPDATE. Interpretation of ``limit`` is owned by the quota
        enforcer:

        * ``None`` → clear the override; enforcer falls back to the
          global default.
        * ``0`` → unlimited.
        * positive → per-user cap.

        The caller is responsible for validating the value; this
        method accepts whatever the admin endpoint has already
        rejected-or-approved.
        """
        db = self._ensure_db()
        await db.execute(
            "UPDATE users SET daily_analysis_limit = ? WHERE id = ?",
            (limit, user_id),
        )
        await db.commit()
        _logger.info(
            "user_daily_limit_set", user_id=user_id, limit=limit
        )

    @staticmethod
    def _row_to_user(row: aiosqlite.Row) -> User:
        """
        Build a User domain object from a fetched ``users`` row.

        ``daily_analysis_limit`` may be missing on databases that
        predate the migration; tolerate its absence so reads never
        crash during a rolling upgrade.
        """
        try:
            limit_raw = row["daily_analysis_limit"]
        except (IndexError, KeyError):
            limit_raw = None
        # Entra SSO columns may be absent on a DB mid-rolling-upgrade;
        # tolerate their absence (NULL/'password') so reads never crash.
        try:
            entra_oid = row["entra_oid"]
        except (IndexError, KeyError):
            entra_oid = None
        try:
            entra_tid = row["entra_tid"]
        except (IndexError, KeyError):
            entra_tid = None
        try:
            auth_source = row["auth_source"] or "password"
        except (IndexError, KeyError):
            auth_source = "password"
        return User(
            id=row["id"],
            email=row["email"],
            hashed_password=row["hashed_password"],
            created_at=datetime.fromisoformat(row["created_at"]),
            daily_analysis_limit=(
                int(limit_raw) if limit_raw is not None else None
            ),
            entra_oid=entra_oid,
            entra_tid=entra_tid,
            auth_source=auth_source,
        )

    # --- Chat Sessions ---

    async def create_session(self, session: ChatSession) -> ChatSession:
        """Persist a new chat session."""
        db = self._ensure_db()
        await db.execute(
            "INSERT INTO chat_sessions "
            "(id, user_id, title, topic, mode, domain, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session.id,
                session.user_id,
                session.title,
                session.topic,
                session.mode,
                session.domain,
                session.created_at.isoformat(),
                session.updated_at.isoformat(),
            ),
        )
        await db.commit()
        _logger.info(
            "session_created",
            session_id=session.id,
            mode=session.mode,
            domain=session.domain,
        )
        return session

    async def get_session(self, session_id: str) -> ChatSession | None:
        """
        Fetch a single session by ID.

        Soft-deleted sessions (``deleted_at`` set) are returned transparently
        — the handler layer decides whether to serve them. The fork-view flow
        for example must still resolve deleted sessions so existing links
        don't 404 mid-conversation, while chat handlers should refuse.
        """
        db = self._ensure_db()
        cursor = await db.execute(
            "SELECT id, user_id, title, topic, mode, domain, "
            "created_at, updated_at, deleted_at "
            "FROM chat_sessions WHERE id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_session(row)

    async def list_sessions(
        self, user_id: str, *, domain: Domain | None = None
    ) -> list[ChatSession]:
        """
        List every non-deleted session for ``user_id``, newest first.

        Soft-deleted rows are filtered out in SQL so the frontend can
        treat the endpoint as authoritative. Run a separate admin tool
        if you ever need to see deleted rows. When ``domain`` is given the
        query additionally restricts to that site's sessions so the two
        public domains never see each other's threads.
        """
        db = self._ensure_db()
        sql = (
            "SELECT id, user_id, title, topic, mode, domain, "
            "created_at, updated_at, deleted_at "
            "FROM chat_sessions "
            "WHERE user_id = ? AND deleted_at IS NULL "
        )
        params: list[str] = [user_id]
        if domain is not None:
            sql += "AND domain = ? "
            params.append(domain)
        sql += "ORDER BY updated_at DESC"
        cursor = await db.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        return [self._row_to_session(r) for r in rows]

    async def update_session_title(self, session_id: str, title: str) -> None:
        """Update the display title of a session."""
        db = self._ensure_db()
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, session_id),
        )
        await db.commit()

    async def update_session_topic(self, session_id: str, topic: str) -> None:
        """
        Persist a topic tag on a session.

        Does not bump ``updated_at`` — topic tagging is metadata, not
        user activity, and must not re-float a session to the top of
        the Recent list.
        """
        db = self._ensure_db()
        await db.execute(
            "UPDATE chat_sessions SET topic = ? WHERE id = ?",
            (topic, session_id),
        )
        await db.commit()
        _logger.info("session_topic_updated", session_id=session_id, topic=topic)

    async def soft_delete_session(self, session_id: str) -> None:
        """
        Mark a session as deleted without removing the row.

        The row — including its messages and any fork tokens — is
        preserved so usage_events and audit queries keep referential
        integrity. Idempotent: re-deleting a session is a no-op.
        """
        db = self._ensure_db()
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE chat_sessions SET deleted_at = ? "
            "WHERE id = ? AND deleted_at IS NULL",
            (now, session_id),
        )
        await db.commit()
        _logger.info("session_soft_deleted", session_id=session_id)

    @staticmethod
    def _row_to_session(row: aiosqlite.Row) -> ChatSession:
        """Convert a database row to a ChatSession model."""
        # topic + deleted_at + mode + domain columns added in migrations;
        # tolerate older rows that don't have them.
        try:
            topic = row["topic"]
        except (IndexError, KeyError):
            topic = None
        try:
            deleted_raw = row["deleted_at"]
        except (IndexError, KeyError):
            deleted_raw = None
        try:
            mode = row["mode"] or DEFAULT_MODE
        except (IndexError, KeyError):
            mode = DEFAULT_MODE
        try:
            domain = row["domain"] or DEFAULT_DOMAIN
        except (IndexError, KeyError):
            domain = DEFAULT_DOMAIN
        deleted_at = (
            datetime.fromisoformat(deleted_raw) if deleted_raw else None
        )
        return ChatSession(
            id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            topic=topic,
            mode=mode,
            domain=domain,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            deleted_at=deleted_at,
        )

    # --- Chat Messages ---

    async def create_message(self, message: ChatMessage) -> ChatMessage:
        """
        Persist a new chat message and bump its session's ``updated_at``.

        Both statements share a single transaction (one ``commit``) so a
        crash between them can never leave an orphaned message with a
        stale session ordering. The previous two-commit form (STORE-4)
        could flush the insert without the bump.
        """
        db = self._ensure_db()
        metadata_json = json.dumps(message.metadata, default=str) if message.metadata else None
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, status, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (message.id, message.session_id, message.role.value, message.content,
             message.status.value, metadata_json, message.created_at.isoformat()),
        )
        await db.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
            (now, message.session_id),
        )
        await db.commit()
        return message

    async def get_message(self, message_id: str) -> ChatMessage | None:
        """Fetch a single message by ID."""
        db = self._ensure_db()
        cursor = await db.execute(
            "SELECT id, session_id, role, content, status, metadata, created_at FROM chat_messages WHERE id = ?",
            (message_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_message(row)

    async def list_messages(self, session_id: str) -> list[ChatMessage]:
        """List all messages in a session, oldest first."""
        db = self._ensure_db()
        cursor = await db.execute(
            "SELECT id, session_id, role, content, status, metadata, created_at FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_message(r) for r in rows]

    async def update_message(
        self,
        message_id: str,
        content: str | None = None,
        status: MessageStatus | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Update a message's content, status, or metadata."""
        db = self._ensure_db()
        updates: list[str] = []
        params: list[object] = []
        if content is not None:
            updates.append("content = ?")
            params.append(content)
        if status is not None:
            updates.append("status = ?")
            params.append(status.value)
        if metadata is not None:
            updates.append("metadata = ?")
            params.append(json.dumps(metadata, default=str))
        if not updates:
            return
        params.append(message_id)
        sql = f"UPDATE chat_messages SET {', '.join(updates)} WHERE id = ?"
        await db.execute(sql, params)
        await db.commit()

    async def claim_message_for_processing(
        self,
        message_id: str,
        *,
        expected_status: MessageStatus,
        claimed_status: MessageStatus,
    ) -> bool:
        """
        Conditionally claim a message via a single guarded UPDATE.

        ``UPDATE ... WHERE id = ? AND status = ?`` only matches when the
        message is still in ``expected_status``; ``rowcount`` then tells
        us whether we won the claim. A redelivered job sees ``0`` and
        aborts before re-running.
        """
        db = self._ensure_db()
        cursor = await db.execute(
            "UPDATE chat_messages SET status = ? WHERE id = ? AND status = ?",
            (claimed_status.value, message_id, expected_status.value),
        )
        await db.commit()
        won = cursor.rowcount == 1
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
        Sweep aged non-terminal messages into FAILED; return (id, user_id).

        A two-step select-then-update keeps the SQL portable (SQLite has
        no UPDATE ... RETURNING before 3.35) and lets us hand the worker
        the exact set of rows it just reaped (joined to the owning user
        via the session) for WS broadcast.
        """
        db = self._ensure_db()
        if not non_terminal:
            return []
        placeholders = ", ".join("?" for _ in non_terminal)
        statuses = [s.value for s in non_terminal]
        cutoff = older_than.isoformat()
        cursor = await db.execute(
            f"SELECT m.id AS id, s.user_id AS user_id "
            f"FROM chat_messages m "
            f"JOIN chat_sessions s ON s.id = m.session_id "
            f"WHERE m.status IN ({placeholders}) AND m.created_at < ?",
            (*statuses, cutoff),
        )
        rows = await cursor.fetchall()
        stale = [(row["id"], row["user_id"]) for row in rows]
        if not stale:
            return []
        stale_ids = [mid for mid, _ in stale]
        id_placeholders = ", ".join("?" for _ in stale_ids)
        await db.execute(
            f"UPDATE chat_messages SET status = ?, content = ? "
            f"WHERE id IN ({id_placeholders})",
            (MessageStatus.FAILED.value, failure_content, *stale_ids),
        )
        await db.commit()
        _logger.info("stale_messages_failed", count=len(stale))
        return stale

    # --- Daily quota counter (atomic reservation ledger) ---

    async def reserve_daily_quota(
        self, user_id: str, day: str, limit: int
    ) -> bool:
        """
        Atomic reserve-if-below-limit via SQLite UPSERT ... RETURNING.

        ``INSERT ... ON CONFLICT DO UPDATE ... WHERE n < limit RETURNING n``
        performs the whole gate in one statement: a fresh row inserts at
        ``n = 1``; an existing row only bumps when it is still under the
        cap. When the conflict WHERE filters the row out (already at cap),
        no row is returned — so the presence of a RETURNING row IS the
        win/lose signal, identical to the Postgres impl. Requires SQLite
        >= 3.35 for RETURNING (shipped 3.45 in our runtime).
        """
        db = self._ensure_db()
        cursor = await db.execute(
            "INSERT INTO daily_quota_counters (user_id, day, n) "
            "VALUES (?, ?, 1) "
            "ON CONFLICT(user_id, day) DO UPDATE SET n = n + 1 "
            "WHERE daily_quota_counters.n < ? "
            "RETURNING n",
            (user_id, day, limit),
        )
        row = await cursor.fetchone()
        await db.commit()
        reserved = row is not None
        if not reserved:
            _logger.info(
                "daily_quota_denied", user_id=user_id, day=day, limit=limit
            )
        return reserved

    async def release_daily_quota(self, user_id: str, day: str) -> None:
        """Give one reserved slot back, clamped at zero."""
        db = self._ensure_db()
        await db.execute(
            "UPDATE daily_quota_counters SET n = MAX(0, n - 1) "
            "WHERE user_id = ? AND day = ?",
            (user_id, day),
        )
        await db.commit()

    async def get_daily_quota_used(self, user_id: str, day: str) -> int:
        """Return the reserved count for the day, or 0 when absent."""
        db = self._ensure_db()
        cursor = await db.execute(
            "SELECT n FROM daily_quota_counters WHERE user_id = ? AND day = ?",
            (user_id, day),
        )
        row = await cursor.fetchone()
        return int(row["n"]) if row else 0

    async def search_messages(
        self, user_id: str, query: str, limit: int = 50
    ) -> list[ChatMessage]:
        """
        Case-insensitive LIKE search across messages owned by ``user_id``.

        Joins through ``chat_sessions`` to enforce ownership — no
        cross-tenant leakage. The query is parameterised so SQL injection
        is not possible; we only wrap it with ``%`` wildcards for LIKE.
        Returns up to ``limit`` rows, newest first.
        """
        db = self._ensure_db()
        pattern = f"%{query}%"
        cursor = await db.execute(
            """
            SELECT m.id, m.session_id, m.role, m.content, m.status, m.metadata, m.created_at
            FROM chat_messages m
            INNER JOIN chat_sessions s ON s.id = m.session_id
            WHERE s.user_id = ?
              AND m.content LIKE ? COLLATE NOCASE
            ORDER BY m.created_at DESC
            LIMIT ?
            """,
            (user_id, pattern, limit),
        )
        rows = await cursor.fetchall()
        return [self._row_to_message(r) for r in rows]

    @staticmethod
    def _row_to_message(row: aiosqlite.Row) -> ChatMessage:
        """Convert a database row to a ChatMessage model."""
        meta_raw = row["metadata"]
        meta = json.loads(meta_raw) if meta_raw else None
        return ChatMessage(
            id=row["id"],
            session_id=row["session_id"],
            role=MessageRole(row["role"]),
            content=row["content"],
            status=MessageStatus(row["status"]),
            metadata=meta,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    # --- Fork Links (Wave 3 C2) ---

    async def create_fork_token(self, session_id: str) -> tuple[str, str]:
        """
        Mint a urlsafe 32-char token for ``session_id``.

        ``secrets.token_urlsafe(24)`` yields ~32 chars of base64 that are
        URL-safe and collision-resistant. The row is inserted unconditionally
        — collisions on a 24-byte secret are astronomically unlikely.
        """
        db = self._ensure_db()
        token = secrets.token_urlsafe(24)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=_FORK_TOKEN_TTL_DAYS)
        await db.execute(
            "INSERT INTO fork_tokens (token, session_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, session_id, now.isoformat(), expires.isoformat()),
        )
        await db.commit()
        _logger.info(
            "fork_token_created",
            session_id=session_id,
            expires_at=expires.isoformat(),
        )
        return token, expires.isoformat()

    async def resolve_fork_token(self, token: str) -> ChatSession | None:
        """
        Return the session behind ``token`` or None if expired/missing.

        Expiration is enforced in Python against current UTC so a clock-
        skewed database file never silently serves stale tokens.
        """
        db = self._ensure_db()
        cursor = await db.execute(
            "SELECT session_id, expires_at FROM fork_tokens WHERE token = ?",
            (token,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        expires = datetime.fromisoformat(row["expires_at"])
        if expires < datetime.now(timezone.utc):
            _logger.info("fork_token_expired", token_prefix=token[:8])
            return None
        return await self.get_session(row["session_id"])

    async def clone_session_for_user(
        self, source_session_id: str, new_user_id: str
    ) -> ChatSession:
        """
        Deep-clone a session into a target user's account.

        Preserves message chronology (iterates ``list_messages`` which is
        already ``ORDER BY created_at ASC``). New IDs are minted for both
        the session and every message so keys never collide with the
        original. Topic is copied so the fork lands in the recipient's
        topic view naturally; title gets a " (fork)" suffix so the UI
        shows both.
        """
        db = self._ensure_db()
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
        await db.execute(
            "INSERT INTO chat_sessions "
            "(id, user_id, title, topic, mode, domain, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_session.id,
                new_session.user_id,
                new_session.title,
                new_session.topic,
                new_session.mode,
                new_session.domain,
                new_session.created_at.isoformat(),
                new_session.updated_at.isoformat(),
            ),
        )
        await db.commit()

        # Copy messages in original chronological order. We avoid calling
        # ``create_message`` because that would bump the session
        # ``updated_at`` on every insert; here we already set it to ``now``.
        source_messages = await self.list_messages(source_session_id)
        for src in source_messages:
            metadata_json = (
                json.dumps(src.metadata, default=str) if src.metadata else None
            )
            await db.execute(
                "INSERT INTO chat_messages (id, session_id, role, content, status, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    generate_id(),
                    new_session.id,
                    src.role.value,
                    src.content,
                    src.status.value,
                    metadata_json,
                    src.created_at.isoformat(),
                ),
            )
        await db.commit()
        _logger.info(
            "session_cloned",
            source=source_session_id,
            target=new_session.id,
            user_id=new_user_id,
            message_count=len(source_messages),
        )
        return new_session

    # --- Telemetry ---

    async def insert_usage_event(self, event: UsageEvent) -> None:
        """
        Persist one telemetry event. Swallows IO errors by design —
        analytics must never block the user-facing hot path.
        """
        db = self._ensure_db()
        props_json = json.dumps(event.properties, default=str) if event.properties else None
        try:
            await db.execute(
                "INSERT INTO usage_events (id, user_id, session_id, event_type, properties, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.id,
                    event.user_id,
                    event.session_id,
                    event.event_type,
                    props_json,
                    event.ts.isoformat(),
                ),
            )
            await db.commit()
        except aiosqlite.Error as exc:
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
        """Count matching events by comparing ISO-8601 timestamps lexically."""
        db = self._ensure_db()
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM usage_events "
            "WHERE user_id = ? AND event_type = ? AND ts >= ?",
            (user_id, event_type, since.isoformat()),
        )
        row = await cursor.fetchone()
        return int(row["n"]) if row else 0

    async def list_usage_events(
        self,
        *,
        event_type: TelemetryEventType | None = None,
        user_id: str | None = None,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[UsageEvent]:
        """
        Read telemetry rows for the admin dashboard.

        Clamps ``limit`` to 5000 so a misbehaving caller cannot pull
        the entire table into memory.
        """
        db = self._ensure_db()
        effective_limit = max(1, min(int(limit), 5000))

        clauses: list[str] = []
        params: list[object] = []
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since.isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT id, user_id, session_id, event_type, properties, ts "
            f"FROM usage_events {where} ORDER BY ts DESC LIMIT ?"
        )
        params.append(effective_limit)
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        return [self._row_to_usage_event(r) for r in rows]

    async def count_users_created_since(self, since: datetime) -> int:
        """Count user rows whose created_at is at or after ``since``."""
        db = self._ensure_db()
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM users WHERE created_at >= ?",
            (since.isoformat(),),
        )
        row = await cursor.fetchone()
        return int(row["n"]) if row else 0

    async def aggregate_engagement_since(
        self,
        since: datetime,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> list[UserEngagement]:
        """
        Per-user event-count rollup via conditional aggregation.

        SQLite ``COUNT(CASE WHEN ...)`` lets us fan out the six tracked
        event types in a single scan. ``MAX(ts)`` gives the "last active"
        timestamp. ``first_seen`` comes from ``users.created_at`` so the
        dashboard can distinguish long-dormant users from fresh ones.
        """
        db = self._ensure_db()
        effective_limit = max(1, min(int(limit), 2000))
        effective_offset = max(0, int(offset))
        cursor = await db.execute(
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
            WHERE e.ts >= ?
            GROUP BY e.user_id, u.email, u.created_at
            ORDER BY last_active DESC
            LIMIT ? OFFSET ?
            """,
            (since.isoformat(), effective_limit, effective_offset),
        )
        rows = await cursor.fetchall()
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
                first_seen=datetime.fromisoformat(row["first_seen"]),
                last_active=(
                    datetime.fromisoformat(row["last_active"])
                    if row["last_active"]
                    else None
                ),
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
        User-role chat messages joined on session + user, newest first.

        Filters out messages belonging to soft-deleted sessions so
        admins don't see questions the user already cleared.
        """
        db = self._ensure_db()
        effective_limit = max(1, min(int(limit), 1000))
        effective_offset = max(0, int(offset))
        cursor = await db.execute(
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
            LIMIT ? OFFSET ?
            """,
            (effective_limit, effective_offset),
        )
        rows = await cursor.fetchall()
        return [
            UserQuestion(
                message_id=row["message_id"],
                session_id=row["session_id"],
                session_title=row["session_title"],
                session_topic=row["session_topic"],
                user_id=row["user_id"],
                user_email=row["user_email"],
                content=row["content"],
                created_at=datetime.fromisoformat(row["created_at"]),
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
        Return every user with a few rollup counters, newest first.

        LEFT JOIN on ``usage_events`` so accounts with zero activity
        still appear — unlike the engagement page, this is the source
        of truth for "who has ever registered".
        """
        db = self._ensure_db()
        effective_limit = max(1, min(int(limit), 1000))
        effective_offset = max(0, int(offset))
        cursor = await db.execute(
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
            LIMIT ? OFFSET ?
            """,
            (effective_limit, effective_offset),
        )
        rows = await cursor.fetchall()
        return [
            UserRecord(
                user_id=row["user_id"],
                email=row["email"],
                created_at=datetime.fromisoformat(row["created_at"]),
                is_admin=row["email"].strip().lower() in admin_emails,
                sessions=int(row["sessions"] or 0),
                messages_sent=int(row["messages_sent"] or 0),
                analyses_completed=int(row["analyses_completed"] or 0),
                last_active=(
                    datetime.fromisoformat(row["last_active"])
                    if row["last_active"]
                    else None
                ),
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
        Aggregate token spend per user via SQLite's ``json_extract``.

        ``COALESCE(..., 0)`` keeps users whose older events pre-date
        token accounting visible (zero token rows) so the dashboard
        can still rank them by analysis count.
        """
        db = self._ensure_db()
        effective_limit = max(1, min(int(limit), 1000))
        effective_offset = max(0, int(offset))
        cursor = await db.execute(
            """
            SELECT
                e.user_id AS user_id,
                u.email AS email,
                COUNT(*) AS analyses,
                COALESCE(SUM(
                    CAST(json_extract(e.properties, '$.input_tokens') AS INTEGER)
                ), 0) AS input_tokens,
                COALESCE(SUM(
                    CAST(json_extract(e.properties, '$.output_tokens') AS INTEGER)
                ), 0) AS output_tokens,
                COALESCE(SUM(
                    CAST(json_extract(e.properties, '$.cache_creation_input_tokens') AS INTEGER)
                ), 0) AS cache_creation_input_tokens,
                COALESCE(SUM(
                    CAST(json_extract(e.properties, '$.cache_read_input_tokens') AS INTEGER)
                ), 0) AS cache_read_input_tokens,
                COALESCE(SUM(
                    CAST(json_extract(e.properties, '$.total_tokens') AS INTEGER)
                ), 0) AS total_tokens
            FROM usage_events e
            JOIN users u ON u.id = e.user_id
            WHERE e.event_type = 'analysis_completed'
              AND e.ts >= ?
            GROUP BY e.user_id, u.email
            ORDER BY total_tokens DESC, analyses DESC
            LIMIT ? OFFSET ?
            """,
            (since.isoformat(), effective_limit, effective_offset),
        )
        rows = await cursor.fetchall()
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
        Per-day total-token counts from ``analysis_completed`` events.

        Gaps are zero-filled so the frontend renders a continuous
        spark line.
        """
        db = self._ensure_db()
        window = max(1, min(int(days), 365))
        since = datetime.now(timezone.utc) - timedelta(days=window - 1)
        since_midnight = since.replace(hour=0, minute=0, second=0, microsecond=0)
        cursor = await db.execute(
            """
            SELECT
                substr(ts, 1, 10) AS day,
                COALESCE(SUM(
                    CAST(json_extract(properties, '$.total_tokens') AS INTEGER)
                ), 0) AS total
            FROM usage_events
            WHERE event_type = 'analysis_completed' AND ts >= ?
            GROUP BY day
            ORDER BY day ASC
            """,
            (since_midnight.isoformat(),),
        )
        rows = await cursor.fetchall()
        totals: dict[str, int] = {
            row["day"]: int(row["total"] or 0) for row in rows
        }
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
        Per-day ``(day, input, output, cache)`` token totals from
        ``analysis_completed`` events. ``cache`` collapses
        cache_creation_input_tokens + cache_read_input_tokens. Missing
        days zero-fill.
        """
        db = self._ensure_db()
        window = max(1, min(int(days), 365))
        since = datetime.now(timezone.utc) - timedelta(days=window - 1)
        since_midnight = since.replace(hour=0, minute=0, second=0, microsecond=0)
        cursor = await db.execute(
            """
            SELECT
                substr(ts, 1, 10) AS day,
                COALESCE(SUM(
                    CAST(json_extract(properties, '$.input_tokens') AS INTEGER)
                ), 0) AS input_tokens,
                COALESCE(SUM(
                    CAST(json_extract(properties, '$.output_tokens') AS INTEGER)
                ), 0) AS output_tokens,
                COALESCE(SUM(
                    CAST(json_extract(properties, '$.cache_creation_input_tokens') AS INTEGER)
                  + CAST(json_extract(properties, '$.cache_read_input_tokens') AS INTEGER)
                ), 0) AS cache_tokens
            FROM usage_events
            WHERE event_type = 'analysis_completed' AND ts >= ?
            GROUP BY day
            ORDER BY day ASC
            """,
            (since_midnight.isoformat(),),
        )
        rows = await cursor.fetchall()
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
        db = self._ensure_db()
        window = max(1, min(int(hours), 24 * 7))
        now = datetime.now(timezone.utc).replace(
            minute=0, second=0, microsecond=0
        )
        since = now - timedelta(hours=window - 1)
        cursor = await db.execute(
            """
            SELECT substr(ts, 1, 13) AS hour, COUNT(*) AS n
            FROM usage_events
            WHERE event_type = ? AND ts >= ?
            GROUP BY hour
            ORDER BY hour ASC
            """,
            (event_type, since.isoformat()),
        )
        rows = await cursor.fetchall()
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
        Aggregate event counts per UTC calendar day across the trailing window.

        The caller is free to re-interpret those UTC buckets in whatever
        timezone makes sense — the dashboard treats them as opaque day
        labels.
        """
        db = self._ensure_db()
        window = max(1, min(int(days), 365))
        since = datetime.now(timezone.utc) - timedelta(days=window - 1)
        since_midnight = since.replace(hour=0, minute=0, second=0, microsecond=0)
        cursor = await db.execute(
            """
            SELECT substr(ts, 1, 10) AS day, COUNT(*) AS n
            FROM usage_events
            WHERE event_type = ? AND ts >= ?
            GROUP BY day
            ORDER BY day ASC
            """,
            (event_type, since_midnight.isoformat()),
        )
        rows = await cursor.fetchall()
        counts: dict[str, int] = {row["day"]: int(row["n"]) for row in rows}
        # Fill gaps so the UI can render a continuous series.
        result: list[tuple[str, int]] = []
        for offset in range(window):
            day = (since_midnight + timedelta(days=offset)).strftime("%Y-%m-%d")
            result.append((day, counts.get(day, 0)))
        return result

    @staticmethod
    def _row_to_usage_event(row: aiosqlite.Row) -> UsageEvent:
        """Convert a database row to a UsageEvent model."""
        props_raw = row["properties"]
        props: dict[str, object] = json.loads(props_raw) if props_raw else {}
        return UsageEvent(
            id=row["id"],
            user_id=row["user_id"],
            session_id=row["session_id"],
            event_type=row["event_type"],
            properties=props,
            ts=datetime.fromisoformat(row["ts"]),
        )

    # --- Feedback ---

    async def insert_feedback(self, entry: FeedbackEntry) -> None:
        """Persist one feedback submission."""
        db = self._ensure_db()
        await db.execute(
            "INSERT INTO feedback_entries (id, user_id, email, topic, content, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                entry.id,
                entry.user_id,
                entry.email,
                entry.topic,
                entry.content,
                entry.status,
                entry.created_at.isoformat(),
            ),
        )
        await db.commit()
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
        """List feedback rows newest-first, with optional filters."""
        db = self._ensure_db()
        effective_limit = max(1, min(int(limit), 1000))
        effective_offset = max(0, int(offset))
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if topic is not None:
            clauses.append("topic = ?")
            params.append(topic)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT id, user_id, email, topic, content, status, created_at "
            f"FROM feedback_entries {where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )
        params.append(effective_limit)
        params.append(effective_offset)
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        return [
            FeedbackEntry(
                id=row["id"],
                user_id=row["user_id"],
                email=row["email"],
                topic=row["topic"],
                content=row["content"],
                status=row["status"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]
