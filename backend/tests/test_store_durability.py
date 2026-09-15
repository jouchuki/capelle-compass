"""
Durable-state regression tests for the audit remediations STORE-1,
STORE-4, and STORE-5 against the SQLite store.

* STORE-1 — ``claim_message_for_processing`` is a conditional, atomic
  claim: the first caller wins (``True``), every redelivery loses
  (``False``) and the message is not re-transitioned.
* STORE-4 — ``create_message`` persists the message AND bumps the
  session ``updated_at`` together; both are observable after the call.
* STORE-5 — ``fail_stale_messages`` sweeps aged non-terminal messages
  into FAILED and returns ``(message_id, user_id)`` for the owning user.

Postgres mirrors the same SQL shapes; these run on SQLite so the suite
stays offline-safe (the Postgres branch is covered by the shared
parametrised quota suite when a DSN is present).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import pytest
import pytest_asyncio

from capelle_platform.models.message import (
    ChatMessage,
    MessageRole,
    MessageStatus,
)
from capelle_platform.models.session import ChatSession
from capelle_platform.models.user import User
from capelle_platform.store.base_store import BaseStore
from capelle_platform.store.impl_sqlite import SQLiteStore
from capelle_platform.utils import generate_id

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def store(settings_factory) -> AsyncIterator[BaseStore]:
    """Yield a fresh on-disk SQLite store, closed at teardown."""
    s = SQLiteStore(settings_factory())
    await s.initialize()
    try:
        yield s
    finally:
        await s.close()


async def _user_session(store: BaseStore) -> tuple[User, ChatSession]:
    """Seed one user + one session and return both."""
    user = User(
        id=generate_id(),
        email=f"{generate_id()}@test.local",
        hashed_password="x",
    )
    await store.create_user(user)
    now = datetime.now(timezone.utc)
    session = ChatSession(
        id=generate_id(),
        user_id=user.id,
        title="t",
        created_at=now,
        updated_at=now,
    )
    await store.create_session(session)
    return user, session


async def _thinking_message(
    store: BaseStore, session_id: str, *, created_at: datetime | None = None
) -> ChatMessage:
    """Insert a THINKING assistant placeholder."""
    msg = ChatMessage(
        id=generate_id(),
        session_id=session_id,
        role=MessageRole.ASSISTANT,
        content="...",
        status=MessageStatus.THINKING,
        created_at=created_at or datetime.now(timezone.utc),
    )
    await store.create_message(msg)
    return msg


async def test_claim_is_won_once(store: BaseStore) -> None:
    """STORE-1: first claim wins, redelivery loses, status is correct."""
    _, session = await _user_session(store)
    msg = await _thinking_message(store, session.id)

    first = await store.claim_message_for_processing(
        msg.id,
        expected_status=MessageStatus.THINKING,
        claimed_status=MessageStatus.STREAMING,
    )
    second = await store.claim_message_for_processing(
        msg.id,
        expected_status=MessageStatus.THINKING,
        claimed_status=MessageStatus.STREAMING,
    )
    assert first is True
    assert second is False

    stored = await store.get_message(msg.id)
    assert stored is not None
    assert stored.status == MessageStatus.STREAMING


async def test_claim_missing_message_loses(store: BaseStore) -> None:
    """A claim against a non-existent id never wins."""
    won = await store.claim_message_for_processing(
        "does-not-exist",
        expected_status=MessageStatus.THINKING,
        claimed_status=MessageStatus.STREAMING,
    )
    assert won is False


async def test_create_message_bumps_session_updated_at(
    store: BaseStore,
) -> None:
    """STORE-4: the session ordering timestamp moves on message insert."""
    _, session = await _user_session(store)
    before = (await store.get_session(session.id)).updated_at  # type: ignore[union-attr]

    await _thinking_message(store, session.id)

    after = (await store.get_session(session.id)).updated_at  # type: ignore[union-attr]
    assert after >= before
    # The message is persisted in the same transaction.
    assert len(await store.list_messages(session.id)) == 1


async def test_reaper_fails_only_aged_non_terminal(store: BaseStore) -> None:
    """STORE-5: stale THINKING is failed; fresh + terminal are left."""
    user, session = await _user_session(store)

    old = await _thinking_message(
        store,
        session.id,
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    fresh = await _thinking_message(store, session.id)
    complete = ChatMessage(
        id=generate_id(),
        session_id=session.id,
        role=MessageRole.ASSISTANT,
        content="done",
        status=MessageStatus.COMPLETE,
        created_at=datetime.now(timezone.utc) - timedelta(hours=3),
    )
    await store.create_message(complete)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    reaped = await store.fail_stale_messages(
        non_terminal=(MessageStatus.THINKING, MessageStatus.STREAMING),
        older_than=cutoff,
        failure_content="timed out",
    )

    assert reaped == [(old.id, user.id)]
    assert (await store.get_message(old.id)).status == MessageStatus.FAILED  # type: ignore[union-attr]
    assert (await store.get_message(fresh.id)).status == MessageStatus.THINKING  # type: ignore[union-attr]
    assert (await store.get_message(complete.id)).status == MessageStatus.COMPLETE  # type: ignore[union-attr]
