"""
Proves the per-user daily-analysis quota is dynamic (reads the DB on
every check) and that ``daily_analysis_limit == 0`` means unlimited.

Since STORE-2 the enforcer RESERVES atomically at check time against a
per-(user, day) counter row — ``assert_allowed`` itself consumes a
slot, rather than counting ``analysis_completed`` events recorded later.
The scenarios below therefore drive the counter purely through
``assert_allowed`` (no event seeding needed) and exercise the new
``release`` refund path.

Scenarios (mirror the task brief):

1. ``test_default_honours_settings_override`` — with
   ``settings.daily_analysis_limit = 5`` the 6th call raises, not the
   4th. Confirms we are NOT hardcoded to 3 anywhere.
2. ``test_per_user_positive_override_beats_settings`` — user cap of 7
   wins against a settings default of 3.
3. ``test_zero_means_unlimited`` — user cap of 0 lets the enforcer
   pass 50 sequential ``assert_allowed`` calls without raising; the
   ``status`` snapshot returns ``limit == 0`` and
   ``remaining == UNLIMITED_REMAINING``.
4. ``test_dynamic_flip_to_unlimited`` — the key test: hit the quota
   under the default, then ``set_user_daily_limit(user, 0)`` and the
   next ``assert_allowed`` must succeed without re-instantiating the
   enforcer. Proves the limit is re-read from the DB every call.
5. ``test_dynamic_drop_back_from_unlimited`` — start at cap 5, burn
   it, drop to 3 at runtime and verify the over-budget call raises with
   the new cap. The mirror image of scenario 4.
6. ``test_reserve_is_atomic_under_concurrency`` — fire N concurrent
   ``assert_allowed`` calls at a one-slot gate; exactly one wins
   (closes the STORE-2 TOCTOU).
7. ``test_release_refunds_a_slot`` — a reserved-then-released slot is
   re-grantable, proving the failed-analysis refund path.

Every test runs against both the SQLite store and, when a
``CAPELLE_POSTGRES_TEST_DSN`` env var is set, against PostgresStore.
No live production DB is touched — the Postgres branch opens a
dedicated pool to the supplied DSN and wipes its tables at teardown.
"""

from __future__ import annotations

import os
from typing import AsyncIterator, Callable

import pytest
import pytest_asyncio

from capelle_platform.models.user import User
from capelle_platform.quota.base_enforcer import (
    UNLIMITED_LIMIT,
    UNLIMITED_REMAINING,
    QuotaExceededError,
)
from capelle_platform.quota.impl_daily import DailyAnalysisQuotaEnforcer
from capelle_platform.settings import Settings
from capelle_platform.store.base_store import BaseStore
from capelle_platform.store.impl_sqlite import SQLiteStore
from capelle_platform.utils import generate_id


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fresh_user(store: BaseStore, *, limit: int | None = None) -> User:
    """
    Register a unique user and, if requested, stamp its per-user limit.

    A fresh email is minted per call so parameterised runs across two
    store backends never collide on the users.email UNIQUE constraint.
    """
    user = User(
        id=generate_id(),
        email=f"{generate_id()}@test.local",
        hashed_password="x",
        daily_analysis_limit=None,
    )
    await store.create_user(user)
    if limit is not None:
        await store.set_user_daily_limit(user.id, limit)
    return user


# ---------------------------------------------------------------------------
# Store parametrisation
# ---------------------------------------------------------------------------


_POSTGRES_DSN = os.environ.get("CAPELLE_POSTGRES_TEST_DSN")


async def _make_sqlite_store(
    settings_factory: Callable[..., Settings],
    **settings_overrides,
) -> tuple[BaseStore, Settings]:
    """
    Build a fresh on-disk SQLite store. The file is created lazily in
    ``settings_factory`` and garbage-collected by the fixture.
    """
    settings = settings_factory(**settings_overrides)
    store = SQLiteStore(settings)
    await store.initialize()
    return store, settings


async def _make_postgres_store(
    settings_factory: Callable[..., Settings],
    **settings_overrides,
) -> tuple[BaseStore, Settings]:
    """
    Build a PostgresStore against the supplied test DSN and truncate
    every table we care about so the test gets a clean slate.

    Skipped entirely when ``CAPELLE_POSTGRES_TEST_DSN`` is unset so
    the suite stays offline-safe.
    """
    if _POSTGRES_DSN is None:
        pytest.skip("CAPELLE_POSTGRES_TEST_DSN not set — skipping pg branch")
    from capelle_platform.store.impl_postgres import PostgresStore

    settings = settings_factory(
        store="postgres",
        postgres_dsn=_POSTGRES_DSN,
        **settings_overrides,
    )
    store = PostgresStore(settings)
    await store.initialize()
    # Clean slate: wipe the rows this suite touches. Order matters for
    # FK constraints (usage_events -> users).
    async with store._pool.acquire() as conn:  # type: ignore[attr-defined]
        await conn.execute("TRUNCATE usage_events CASCADE")
        await conn.execute("TRUNCATE daily_quota_counters CASCADE")
        await conn.execute("TRUNCATE users CASCADE")
    return store, settings


@pytest_asyncio.fixture(params=["sqlite", "postgres"])
async def store_and_settings(
    request, settings_factory
) -> AsyncIterator[tuple[BaseStore, Settings]]:
    """
    Yield ``(store, settings)`` for each backend.

    Postgres is skipped (not failed) when the test DSN is missing so
    developers without a local PG can still run the suite.
    """
    backend = request.param
    if backend == "sqlite":
        store, settings = await _make_sqlite_store(settings_factory)
    else:
        store, settings = await _make_postgres_store(settings_factory)
    try:
        yield store, settings
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


async def test_default_honours_settings_override(
    settings_factory,
) -> None:
    """
    Scenario 1: raise the global default to 5 and verify the enforcer
    lets 5 analyses through before raising on the 6th.

    This is the "not hardcoded to 3" proof. We read ``Settings`` off
    the same object we hand the enforcer, so no env coupling.
    """
    settings = settings_factory(daily_analysis_limit=5)
    store = SQLiteStore(settings)
    await store.initialize()
    try:
        user = await _fresh_user(store)
        enforcer = DailyAnalysisQuotaEnforcer(store, settings)

        # Reserve 5 analyses — each assert_allowed consumes a slot.
        for i in range(5):
            snap = await enforcer.assert_allowed(user.id)
            assert snap.limit == 5, f"call {i}: limit drifted to {snap.limit}"

        # 6th call must raise — the cap is 5, not the hardcoded 3.
        with pytest.raises(QuotaExceededError) as excinfo:
            await enforcer.assert_allowed(user.id)
        assert excinfo.value.status.limit == 5
        assert excinfo.value.status.used == 5
    finally:
        await store.close()


async def test_per_user_positive_override_beats_settings(
    store_and_settings,
) -> None:
    """
    Scenario 2: settings default of 3 + per-user override of 7 ==
    the user gets 7 analyses, raise on the 8th.
    """
    store, settings = store_and_settings
    # Pin the default low so the 7 would-be successes clearly come from
    # the per-user override rather than coincidental settings drift.
    assert settings.daily_analysis_limit == 3
    user = await _fresh_user(store, limit=7)
    enforcer = DailyAnalysisQuotaEnforcer(store, settings)

    for i in range(7):
        snap = await enforcer.assert_allowed(user.id)
        assert snap.limit == 7, f"call {i}: expected cap 7, got {snap.limit}"

    with pytest.raises(QuotaExceededError):
        await enforcer.assert_allowed(user.id)


async def test_zero_means_unlimited(store_and_settings) -> None:
    """
    Scenario 3: a ``daily_analysis_limit = 0`` override is unlimited.

    Running ``assert_allowed`` 50 times in a row without a single
    raise, and verifying ``status`` reports the sentinel payload.
    """
    store, settings = store_and_settings
    user = await _fresh_user(store, limit=0)
    enforcer = DailyAnalysisQuotaEnforcer(store, settings)

    for i in range(50):
        snap = await enforcer.assert_allowed(user.id)
        assert snap.limit == UNLIMITED_LIMIT, (
            f"call {i}: unlimited user saw cap {snap.limit}"
        )
        assert snap.remaining == UNLIMITED_REMAINING

    # Also assert the standalone status() path yields the sentinel.
    status = await enforcer.status(user.id)
    assert status.limit == 0
    assert status.remaining == UNLIMITED_REMAINING


async def test_dynamic_flip_to_unlimited(store_and_settings) -> None:
    """
    Scenario 4 — the headline case. Start a user under the default
    cap of 3, burn it, confirm ``QuotaExceededError``. Then flip the
    DB column to ``0`` without reconstructing the enforcer. The very
    next ``assert_allowed`` must succeed.

    If this test fails the enforcer is caching the cap and the user's
    "is my quota hardcoded to 3" fear is justified.
    """
    store, settings = store_and_settings
    user = await _fresh_user(store)  # no override -> settings default (3)
    enforcer = DailyAnalysisQuotaEnforcer(store, settings)

    # Burn the cap.
    for _ in range(3):
        await enforcer.assert_allowed(user.id)
    with pytest.raises(QuotaExceededError):
        await enforcer.assert_allowed(user.id)

    # Flip to unlimited AT RUNTIME — no new enforcer instance.
    await store.set_user_daily_limit(user.id, 0)

    # Same enforcer instance — must succeed on the very next call.
    snap = await enforcer.assert_allowed(user.id)
    assert snap.limit == UNLIMITED_LIMIT
    assert snap.remaining == UNLIMITED_REMAINING

    # And again, a few times, to make the point.
    for _ in range(10):
        snap = await enforcer.assert_allowed(user.id)
        assert snap.limit == UNLIMITED_LIMIT


async def test_dynamic_drop_back_from_unlimited(store_and_settings) -> None:
    """
    Scenario 5 — reverse of scenario 4. Start at a generous cap (10),
    reserve all 10 slots, then drop the user to cap=3 via the store. The
    next ``assert_allowed`` must raise against the NEW cap, and
    ``status`` must reflect it — again without a fresh enforcer instance.

    (Starting at a positive cap rather than unlimited is the meaningful
    test now that the ledger is reservation-based: unlimited never
    touches the counter, so there would be nothing to "drop back" onto.)
    """
    store, settings = store_and_settings
    user = await _fresh_user(store, limit=10)
    enforcer = DailyAnalysisQuotaEnforcer(store, settings)

    # Reserve all 10 slots.
    for _ in range(10):
        await enforcer.assert_allowed(user.id)

    # Drop to cap=3 at runtime.
    await store.set_user_daily_limit(user.id, 3)

    status = await enforcer.status(user.id)
    assert status.limit == 3, f"status did not re-read DB: {status}"
    # 10 reserved >> 3 → remaining must clamp to 0.
    assert status.used == 10
    assert status.remaining == 0

    with pytest.raises(QuotaExceededError) as excinfo:
        await enforcer.assert_allowed(user.id)
    assert excinfo.value.status.limit == 3
    assert excinfo.value.status.used == 10


async def test_reserve_is_atomic_under_concurrency(store_and_settings) -> None:
    """
    Scenario 6 — the STORE-2 TOCTOU proof. Fire many concurrent
    ``assert_allowed`` calls at a one-slot gate; exactly one must win
    and the rest must raise ``QuotaExceededError``.
    """
    import asyncio

    store, settings = store_and_settings
    user = await _fresh_user(store, limit=1)
    enforcer = DailyAnalysisQuotaEnforcer(store, settings)

    results = await asyncio.gather(
        *(enforcer.assert_allowed(user.id) for _ in range(20)),
        return_exceptions=True,
    )
    wins = [r for r in results if not isinstance(r, Exception)]
    denials = [r for r in results if isinstance(r, QuotaExceededError)]
    assert len(wins) == 1, f"expected exactly one winner, got {len(wins)}"
    assert len(denials) == 19
    assert (await enforcer.status(user.id)).used == 1


async def test_release_refunds_a_slot(store_and_settings) -> None:
    """
    Scenario 7 — the failed-analysis refund path. Burn the cap, confirm
    denial, release one slot, then the next reservation succeeds again.
    """
    store, settings = store_and_settings
    user = await _fresh_user(store, limit=2)
    enforcer = DailyAnalysisQuotaEnforcer(store, settings)

    await enforcer.assert_allowed(user.id)
    await enforcer.assert_allowed(user.id)
    with pytest.raises(QuotaExceededError):
        await enforcer.assert_allowed(user.id)

    # Simulate a failed analysis giving its slot back.
    await enforcer.release(user.id)
    assert (await enforcer.status(user.id)).used == 1

    snap = await enforcer.assert_allowed(user.id)
    assert snap.used == 2
