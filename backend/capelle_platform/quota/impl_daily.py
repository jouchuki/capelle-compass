"""
Calendar-day quota enforcer anchored in a configurable timezone.

The "day" starts at midnight in ``Settings.quota_timezone``; the MVP
uses ``Europe/Amsterdam`` so a Dutch municipal worker sees their
allowance reset overnight.

Enforcement is an atomic per-(user, day) reservation (STORE-2): each
``assert_allowed`` increments a durable counter row in the same step
that decides whether budget remains, so concurrent requests cannot all
slip through a one-slot gate. A failed analysis releases its slot via
:meth:`release` so failed analyses are NOT counted. Daily reset is
implicit — every local day uses a fresh bucket key, so yesterday's
counter is simply never read again.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from capelle_platform.observability import get_logger
from capelle_platform.quota.base_enforcer import (
    UNLIMITED_LIMIT,
    UNLIMITED_REMAINING,
    BaseQuotaEnforcer,
    QuotaExceededError,
    QuotaStatus,
)
from capelle_platform.settings import Settings
from capelle_platform.store.base_store import BaseStore

_logger = get_logger(__name__)


class DailyAnalysisQuotaEnforcer(BaseQuotaEnforcer):
    """
    Count ``analysis_completed`` events since the last local midnight.

    The effective cap is looked up **per call**:

    1. Fetch the user row via :meth:`BaseStore.get_user_by_id`.
    2. If ``user.daily_analysis_limit is None`` → fall back to
       :attr:`Settings.daily_analysis_limit`.
    3. Else the user's value wins. ``0`` means
       :data:`UNLIMITED_LIMIT` — :meth:`assert_allowed` never raises
       and :meth:`status` returns a sentinel "plenty left" snapshot.

    Per-call lookup (rather than constructor capture) is deliberate:
    admins may flip an override at runtime and the next request MUST
    honour it without a service restart.

    Thread-safe: holds no mutable state, so a single instance can back
    many worker processes without coordination. Delegates all
    persistence through the ``BaseStore``; does not add a dedicated
    store method just to read the override.
    """

    def __init__(self, store: BaseStore, settings: Settings) -> None:
        """
        Construct with the shared store + settings.

        The timezone string is resolved eagerly so a misconfigured
        IANA name fails fast at startup rather than on the first
        request. The global default is read every call via
        :attr:`_settings` — no longer captured as ``self._limit`` —
        because the quota is now per-user.
        """
        self._store = store
        self._settings = settings
        self._tz = ZoneInfo(settings.quota_timezone)

    def _day_key(self) -> str:
        """
        Return today's local-calendar-day bucket key (``YYYY-MM-DD``).

        This is the partition key for the atomic per-(user, day) quota
        counter. It is derived from the *local* date in
        ``quota_timezone`` so the counter naturally rolls over at local
        midnight — the daily-reset logic is "use a new bucket key", with
        no sweep job required. Old buckets simply stop being read.
        """
        return datetime.now(self._tz).strftime("%Y-%m-%d")

    def _next_reset_utc(self) -> datetime:
        """Return the next local-midnight instant as a UTC datetime."""
        now_local = datetime.now(self._tz)
        tomorrow_local = (now_local + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return tomorrow_local.astimezone(ZoneInfo("UTC"))

    async def _effective_limit(self, user_id: str) -> int:
        """
        Resolve the cap that applies to ``user_id`` for this request.

        Falls back to ``Settings.daily_analysis_limit`` when the user
        row has no override (``daily_analysis_limit is None``) or when
        the user no longer exists (defensive — a quota check on a
        stale user id should not crash the request). ``0`` is passed
        through verbatim; callers check with :data:`UNLIMITED_LIMIT`.
        """
        user = await self._store.get_user_by_id(user_id)
        if user is None or user.daily_analysis_limit is None:
            return int(self._settings.daily_analysis_limit)
        return int(user.daily_analysis_limit)

    async def status(self, user_id: str) -> QuotaStatus:
        """
        Return the quota snapshot for ``user_id`` WITHOUT mutating state.

        Reads the atomic per-(user, day) counter (STORE-2/STORE-6) — the
        same durable ledger ``assert_allowed`` reserves against — so the
        pill the UI renders and the gate the handler enforces can never
        disagree. Unlimited (``limit == 0``) short-circuits the read so a
        heavy user never pays the DB roundtrip just to see "onbeperkt".
        """
        limit = await self._effective_limit(user_id)
        resets_at = self._next_reset_utc()
        if limit == UNLIMITED_LIMIT:
            return QuotaStatus(
                used=0,
                limit=UNLIMITED_LIMIT,
                remaining=UNLIMITED_REMAINING,
                resets_at=resets_at,
            )
        used = await self._store.get_daily_quota_used(user_id, self._day_key())
        remaining = max(0, limit - used)
        return QuotaStatus(
            used=used,
            limit=limit,
            remaining=remaining,
            resets_at=resets_at,
        )

    async def assert_allowed(self, user_id: str) -> QuotaStatus:
        """
        Atomically reserve one slot, or raise ``QuotaExceededError``.

        Users with an unlimited override (``limit == 0``) are allowed
        unconditionally without touching the counter. For every other
        cap the reservation is a single atomic store call
        (``reserve_daily_quota``) that increments-if-below-limit and
        tells us whether we won — closing the check-then-act race that
        let N concurrent ``send_message`` calls all pass a one-slot gate.
        """
        limit = await self._effective_limit(user_id)
        resets_at = self._next_reset_utc()
        if limit == UNLIMITED_LIMIT:
            return QuotaStatus(
                used=0,
                limit=UNLIMITED_LIMIT,
                remaining=UNLIMITED_REMAINING,
                resets_at=resets_at,
            )
        day = self._day_key()
        reserved = await self._store.reserve_daily_quota(user_id, day, limit)
        used = await self._store.get_daily_quota_used(user_id, day)
        if not reserved:
            snapshot = QuotaStatus(
                used=used,
                limit=limit,
                remaining=max(0, limit - used),
                resets_at=resets_at,
            )
            _logger.info(
                "quota_exceeded",
                user_id=user_id,
                used=snapshot.used,
                limit=snapshot.limit,
            )
            raise QuotaExceededError(snapshot)
        return QuotaStatus(
            used=used,
            limit=limit,
            remaining=max(0, limit - used),
            resets_at=resets_at,
        )

    async def release(self, user_id: str) -> None:
        """
        Return one reserved slot; best-effort, never raises.

        Called from the worker's terminal-failure path. The day bucket is
        recomputed here — a job that fails close to local midnight will
        release against the current bucket, which is the same bucket it
        almost certainly reserved against; the zero-floor in the store
        keeps any edge case harmless.
        """
        try:
            await self._store.release_daily_quota(user_id, self._day_key())
        except Exception:  # pragma: no cover - cleanup path must not throw
            _logger.warning("quota_release_failed", user_id=user_id)
