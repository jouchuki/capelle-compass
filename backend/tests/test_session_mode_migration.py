"""
Tests for the ``mode`` column on chat_sessions — round-trip + backward-compat.

The migration adds a NOT NULL column with a 'groeikern' default so legacy
rows (created before the column existed) keep behaving as they always did
without a one-shot backfill job. The tests below verify three things:

1. A fresh SQLiteStore writes/reads the column on every chat session.
2. A legacy DB created without the column survives ``initialize()`` —
   the migration adds the column and pre-existing rows surface as
   ``mode='groeikern'``.
3. ``clone_session_for_user`` preserves the source's mode (jeugdzorg
   forks must stay jeugdzorg, not silently flip to the default).
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from capelle_platform.models import ChatSession, User
from capelle_platform.models.mode import DEFAULT_MODE
from capelle_platform.store.impl_sqlite import SQLiteStore
from capelle_platform.utils import generate_id


def _legacy_db(path: Path) -> None:
    """Materialise a chat_sessions schema as it existed before the mode column.

    The schema string mirrors impl_sqlite._CREATE_TABLES_SQL prior to the
    mode-column change. We insert one user + one session so the migration
    has something to backfill.
    """
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE users (
            id                   TEXT PRIMARY KEY,
            email                TEXT UNIQUE NOT NULL,
            hashed_password      TEXT NOT NULL,
            created_at           TEXT NOT NULL,
            daily_analysis_limit INTEGER
        );
        CREATE TABLE chat_sessions (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            title       TEXT NOT NULL,
            topic       TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """
    )
    conn.execute(
        "INSERT INTO users VALUES(?, ?, ?, ?, NULL)",
        ("u-legacy", "legacy@test", "x", "2024-01-01T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO chat_sessions VALUES(?, ?, ?, NULL, ?, ?)",
        (
            "s-legacy",
            "u-legacy",
            "Pre-mode session",
            "2024-01-01T00:00:00+00:00",
            "2024-01-01T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_round_trip_groeikern_and_jeugdzorg(settings_factory) -> None:
    settings = settings_factory()
    store = SQLiteStore(settings)
    await store.initialize()
    try:
        user = User(
            id=generate_id(),
            email="rt@test",
            hashed_password="x",
            created_at=datetime.now(timezone.utc),
        )
        await store.create_user(user)

        gk = ChatSession(id=generate_id(), user_id=user.id, title="gk")
        await store.create_session(gk)
        jz = ChatSession(
            id=generate_id(), user_id=user.id, title="jz", mode="jeugdzorg"
        )
        await store.create_session(jz)

        fetched_gk = await store.get_session(gk.id)
        fetched_jz = await store.get_session(jz.id)
        assert fetched_gk is not None and fetched_gk.mode == "groeikern"
        assert fetched_jz is not None and fetched_jz.mode == "jeugdzorg"

        # list_sessions returns the per-session mode too — the sidebar
        # never sees fresh ChatSession objects out of get_session, only
        # this list-shaped one.
        listed = await store.list_sessions(user.id)
        by_id = {s.id: s.mode for s in listed}
        assert by_id[gk.id] == "groeikern"
        assert by_id[jz.id] == "jeugdzorg"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_legacy_db_migrates_existing_rows_to_groeikern(
    settings_factory, tmp_path: Path
) -> None:
    db_path = tmp_path / "legacy.db"
    _legacy_db(db_path)

    settings = settings_factory(sqlite_path=db_path)
    store = SQLiteStore(settings)
    # initialize() runs the migration; the pre-existing 'Pre-mode session'
    # row must come back with mode set to the configured default.
    await store.initialize()
    try:
        session = await store.get_session("s-legacy")
        assert session is not None
        assert session.mode == DEFAULT_MODE == "groeikern"
        assert session.title == "Pre-mode session"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_clone_preserves_mode(settings_factory) -> None:
    settings = settings_factory()
    store = SQLiteStore(settings)
    await store.initialize()
    try:
        source_user = User(
            id=generate_id(),
            email="src@test",
            hashed_password="x",
            created_at=datetime.now(timezone.utc),
        )
        target_user = User(
            id=generate_id(),
            email="tgt@test",
            hashed_password="x",
            created_at=datetime.now(timezone.utc),
        )
        await store.create_user(source_user)
        await store.create_user(target_user)

        source = ChatSession(
            id=generate_id(),
            user_id=source_user.id,
            title="forecast plan",
            mode="jeugdzorg",
        )
        await store.create_session(source)

        cloned = await store.clone_session_for_user(source.id, target_user.id)
        assert cloned.mode == "jeugdzorg"
        assert cloned.user_id == target_user.id
        assert cloned.id != source.id

        fetched = await store.get_session(cloned.id)
        assert fetched is not None and fetched.mode == "jeugdzorg"
    finally:
        await store.close()


def _sync(coro):
    return asyncio.get_event_loop().run_until_complete(coro)
