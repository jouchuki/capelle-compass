"""
One-shot SQLite -> Postgres data migration for the Capelle Platform.

Walks every table the platform owns in dependency order (users first,
then sessions, then messages / fork_tokens / usage_events, then
feedback) and bulk-inserts each row into the target Postgres database
via ``executemany``. Idempotent thanks to ``ON CONFLICT DO NOTHING``
on the primary key, so re-running the script after a partial copy is
safe.

Usage
-----

    python3 scripts/migrate_sqlite_to_postgres.py [SQLITE_PATH] [POSTGRES_DSN]

Defaults:
    SQLITE_PATH   = /opt/backend/capelle_platform.db
    POSTGRES_DSN  = $CAPELLE_POSTGRES_DSN
                    (else postgresql://capelle:capelle@aoi-todo:5432/capelle)

The script is deliberately self-contained — only ``asyncpg`` and
``aiosqlite`` on top of the standard library — so it runs from any
environment that can reach both databases.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Final

import aiosqlite
import asyncpg

_DEFAULT_SQLITE_PATH: Final[str] = "/opt/backend/capelle_platform.db"
_DEFAULT_DSN: Final[str] = ""

# Ordered so parents land before children — FK references point only
# backwards through this list.
_TABLE_ORDER: Final[tuple[str, ...]] = (
    "users",
    "chat_sessions",
    "chat_messages",
    "fork_tokens",
    "usage_events",
    "feedback_entries",
)

# Columns we care about per table. Order matters — it's the order of
# the INSERT placeholders. Data-driven so the COPY loop is six lines
# instead of six near-identical blocks.
_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "users": ("id", "email", "hashed_password", "created_at"),
    "chat_sessions": (
        "id", "user_id", "title", "topic",
        "created_at", "updated_at", "deleted_at",
    ),
    "chat_messages": (
        "id", "session_id", "role", "content", "status",
        "metadata", "created_at",
    ),
    "fork_tokens": ("token", "session_id", "created_at", "expires_at"),
    "usage_events": (
        "id", "user_id", "session_id", "event_type", "properties", "ts",
    ),
    "feedback_entries": (
        "id", "user_id", "email", "topic", "content", "status", "created_at",
    ),
}

# Timestamp columns per table — we parse ISO-8601 text from SQLite and
# hand Postgres a proper UTC-aware datetime. Postgres refuses to bind
# naive datetimes to TIMESTAMPTZ.
_DATETIME_COLS: Final[dict[str, frozenset[str]]] = {
    "users": frozenset({"created_at"}),
    "chat_sessions": frozenset({"created_at", "updated_at", "deleted_at"}),
    "chat_messages": frozenset({"created_at"}),
    "fork_tokens": frozenset({"created_at", "expires_at"}),
    "usage_events": frozenset({"ts"}),
    "feedback_entries": frozenset({"created_at"}),
}

# JSON columns per table — SQLite stored them as TEXT, Postgres wants
# a Python object (which asyncpg's JSONB codec will encode).
_JSON_COLS: Final[dict[str, frozenset[str]]] = {
    "users": frozenset(),
    "chat_sessions": frozenset(),
    "chat_messages": frozenset({"metadata"}),
    "fork_tokens": frozenset(),
    "usage_events": frozenset({"properties"}),
    "feedback_entries": frozenset(),
}

# Which column is the primary key — used for ON CONFLICT resolution.
_PRIMARY_KEY: Final[dict[str, str]] = {
    "users": "id",
    "chat_sessions": "id",
    "chat_messages": "id",
    "fork_tokens": "token",
    "usage_events": "id",
    "feedback_entries": "id",
}


class SqliteToPostgresMigrator:
    """
    Drives the one-shot migration.

    Builds the Postgres schema (idempotent ``CREATE TABLE IF NOT EXISTS``
    via PostgresStore's ``schema.sql``), walks each table in
    FK-safe order, and reports row counts at the end so the operator
    can eyeball parity against the source DB.
    """

    def __init__(self, sqlite_path: str, postgres_dsn: str) -> None:
        """Record the source + target locations. No IO until :meth:`run`."""
        self._sqlite_path = sqlite_path
        self._postgres_dsn = postgres_dsn
        self._copied: dict[str, int] = {name: 0 for name in _TABLE_ORDER}

    async def run(self) -> None:
        """Bootstrap schema, copy every table, print row counts."""
        print(f"[migrate] source sqlite = {self._sqlite_path}")
        print(f"[migrate] target postgres = {self._redact_dsn(self._postgres_dsn)}")

        pg_pool = await asyncpg.create_pool(
            dsn=self._postgres_dsn,
            min_size=1,
            max_size=4,
            init=self._init_connection,
        )
        try:
            await self._bootstrap_schema(pg_pool)
            async with aiosqlite.connect(self._sqlite_path) as sqlite_conn:
                sqlite_conn.row_factory = aiosqlite.Row
                for table in _TABLE_ORDER:
                    count = await self._copy_table(sqlite_conn, pg_pool, table)
                    self._copied[table] = count
                    print(f"[migrate] {table}: {count} rows copied")
        finally:
            await pg_pool.close()

        print("[migrate] done")
        total = sum(self._copied.values())
        print(f"[migrate] total rows copied: {total}")
        for table, count in self._copied.items():
            print(f"  - {table}: {count}")

    async def _bootstrap_schema(self, pool: asyncpg.Pool) -> None:
        """
        Execute ``store/schema.sql`` so the target is guaranteed ready.

        Locates the SQL file relative to this script so the migration
        stays in lockstep with the PostgresStore's idea of the schema.
        """
        here = os.path.dirname(os.path.abspath(__file__))
        schema_path = os.path.normpath(
            os.path.join(
                here,
                "..",
                "backend",
                "capelle_platform",
                "store",
                "schema.sql",
            )
        )
        with open(schema_path, encoding="utf-8") as fh:
            schema_sql = fh.read()
        async with pool.acquire() as conn:
            await conn.execute(schema_sql)
        print(f"[migrate] schema bootstrapped from {schema_path}")

    async def _copy_table(
        self,
        sqlite_conn: aiosqlite.Connection,
        pg_pool: asyncpg.Pool,
        table: str,
    ) -> int:
        """
        Copy one table's rows with ``executemany`` + ``ON CONFLICT DO NOTHING``.

        Tolerates missing columns on pre-migration source DBs (older
        SQLite files lack ``topic`` / ``deleted_at``) by filling NULL
        on the target side.
        """
        columns = _COLUMNS[table]
        pk = _PRIMARY_KEY[table]

        present = await self._sqlite_columns(sqlite_conn, table)
        if not present:
            print(f"[migrate] {table}: source table not present, skipping")
            return 0

        select_cols = [c for c in columns if c in present]
        missing = [c for c in columns if c not in present]
        if missing:
            print(f"[migrate] {table}: source missing {missing}, "
                  f"filling with NULL on target")

        select_sql = f"SELECT {', '.join(select_cols)} FROM {table}"
        cursor = await sqlite_conn.execute(select_sql)
        rows = await cursor.fetchall()
        if not rows:
            return 0

        batch: list[tuple[Any, ...]] = []
        for row in rows:
            payload: list[Any] = []
            for col in columns:
                raw = row[col] if col in select_cols else None
                payload.append(self._coerce(table, col, raw))
            batch.append(tuple(payload))

        placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))
        insert_sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT ({pk}) DO NOTHING"
        )
        async with pg_pool.acquire() as conn:
            await conn.executemany(insert_sql, batch)
        return len(batch)

    @staticmethod
    async def _sqlite_columns(
        conn: aiosqlite.Connection, table: str
    ) -> frozenset[str]:
        """Return the column names present on ``table`` in the source DB."""
        try:
            cursor = await conn.execute(f"PRAGMA table_info({table})")
            cols = {row["name"] for row in await cursor.fetchall()}
        except aiosqlite.Error:
            return frozenset()
        return frozenset(cols)

    @staticmethod
    def _coerce(table: str, col: str, value: Any) -> Any:
        """
        Coerce a SQLite cell value into something Postgres accepts.

        * ISO-8601 strings -> aware UTC datetimes (for TIMESTAMPTZ).
        * JSON TEXT -> Python object (JSONB codec handles the rest).
        * Everything else passes through unchanged.
        """
        if value is None:
            return None
        if col in _DATETIME_COLS.get(table, frozenset()):
            if isinstance(value, datetime):
                if value.tzinfo:
                    return value
                return value.replace(tzinfo=timezone.utc)
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo:
                return parsed
            return parsed.replace(tzinfo=timezone.utc)
        if col in _JSON_COLS.get(table, frozenset()):
            if isinstance(value, (dict, list)):
                return value
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                # Preserve the raw string rather than drop data — it lands
                # in JSONB as a JSON string literal.
                return json.loads(json.dumps(value))
        return value

    @staticmethod
    async def _init_connection(conn: asyncpg.Connection) -> None:
        """Register JSONB codec so dicts round-trip cleanly."""
        await conn.set_type_codec(
            "jsonb",
            encoder=lambda v: json.dumps(v, default=str),
            decoder=json.loads,
            schema="pg_catalog",
        )

    @staticmethod
    def _redact_dsn(dsn: str) -> str:
        """Mask the password portion of a DSN for console output."""
        if "@" not in dsn or "://" not in dsn:
            return dsn
        prefix, rest = dsn.split("://", 1)
        creds, tail = rest.split("@", 1)
        if ":" in creds:
            user, _ = creds.split(":", 1)
            return f"{prefix}://{user}:***@{tail}"
        return dsn

    @classmethod
    def from_argv(cls, argv: list[str]) -> "SqliteToPostgresMigrator":
        """
        Build an instance from ``sys.argv``-style positional args.

        Falls back to ``CAPELLE_POSTGRES_DSN`` for the target DSN and
        to hard-coded sensible defaults when neither the CLI nor the
        environment supplies a value.
        """
        sqlite_path = argv[1] if len(argv) > 1 else _DEFAULT_SQLITE_PATH
        if len(argv) > 2:
            dsn = argv[2]
        else:
            dsn = os.environ.get("CAPELLE_POSTGRES_DSN", _DEFAULT_DSN)
        return cls(sqlite_path, dsn)


def _entrypoint() -> int:
    """Thin CLI wrapper — the only module-level function in this file."""
    migrator = SqliteToPostgresMigrator.from_argv(sys.argv)
    try:
        asyncio.run(migrator.run())
    except (asyncpg.PostgresError, aiosqlite.Error) as exc:
        print(f"[migrate] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_entrypoint())
