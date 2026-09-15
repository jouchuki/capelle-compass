"""
Abstract contract for daily analysis-quota enforcement.

The enforcer answers two questions:
    * ``status(user_id)`` — how much of the quota is left, and when
      does it reset?
    * ``assert_allowed(user_id)`` — raise if the user has no remaining
      budget for today.

Separating status from enforcement lets the UI render the "X of 3
analyses used" pill without ever triggering a 429.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Final

# Sentinel for "no cap at all" in the per-user daily-analysis override.
#
# We overload ``limit == 0`` on both the persisted column and the
# :class:`QuotaStatus` payload to mean "unlimited". Zero is chosen
# deliberately: it is the only integer that admits a human-readable
# "off" reading and that has no meaningful operational use as a real
# cap (a zero cap would lock the user out entirely, which is already
# achievable by simply not giving them an account). Keeping the
# constant here — rather than a magic ``0`` scattered through impl
# files — gives us one place to document the contract and one place
# to rename if we ever extend the sentinel vocabulary.
UNLIMITED_LIMIT: Final[int] = 0

# Large finite sentinel the frontend's QuotaPill sees when a user has
# an unlimited cap. Using a huge number (rather than e.g. ``math.inf``
# which cannot round-trip JSON) lets the existing pill render
# ``remaining = 999999`` as "plenty left" without special-casing.
# The pill is expected to detect ``limit == 0`` and render "onbeperkt"
# (unlimited) — the remaining value is a belt-and-braces fallback.
UNLIMITED_REMAINING: Final[int] = 999_999


@dataclass(frozen=True)
class QuotaStatus:
    """
    Snapshot of a user's daily quota position.

    Attributes:
        used: Number of successful analyses counted against today.
        limit: Maximum allowed per day. ``0`` is the sentinel for
            "unlimited" — see :data:`UNLIMITED_LIMIT`.
        remaining: ``max(0, limit - used)``. Pre-computed so callers
            don't duplicate the clamp. When ``limit == 0`` (unlimited),
            implementations set this to :data:`UNLIMITED_REMAINING`
            so existing UI renders "plenty left" without special
            casing the numeric field.
        resets_at: Next reset instant in UTC. Rendered in the UI as
            "reset om HH:MM" in the user's locale.
    """

    used: int
    limit: int
    remaining: int
    resets_at: datetime


class QuotaExceededError(Exception):
    """
    Raised by ``assert_allowed`` when the user's daily budget is
    exhausted.

    Attributes:
        status: Current ``QuotaStatus`` so the caller can return the
            reset time in an HTTP header / response body.
    """

    def __init__(self, status: QuotaStatus) -> None:
        super().__init__(
            f"Daily quota exceeded ({status.used}/{status.limit})"
        )
        self.status = status


class BaseQuotaEnforcer(ABC):
    """
    Per-user daily quota contract.

    Implementations decide what "day" means (UTC, Europe/Amsterdam,
    rolling 24h) and what "analysis" means (message_sent vs
    analysis_completed). Handlers depend only on this ABC.
    """

    @abstractmethod
    async def status(self, user_id: str) -> QuotaStatus:
        """
        Return the user's current quota snapshot without mutating state.

        Safe to call on every page load; implementations may cache but
        MUST NOT memoise past the reset boundary.
        """

    @abstractmethod
    async def assert_allowed(self, user_id: str) -> QuotaStatus:
        """
        Atomically RESERVE budget for one more analysis.

        Unlike a read-only check, this mutates state: it claims one slot
        of the user's daily quota in the same atomic step that decides
        whether budget remains, so N concurrent callers can never all
        pass a one-slot gate (closing the STORE-2 TOCTOU). Raises
        ``QuotaExceededError`` when the limit is already hit; on success
        returns the post-reservation ``QuotaStatus`` so the caller can
        surface ``remaining`` without a second round-trip.

        Callers whose analysis ultimately FAILS must call
        :meth:`release` to return the reserved slot — failed analyses do
        not count against the daily cap.
        """

    @abstractmethod
    async def release(self, user_id: str) -> None:
        """
        Return one previously-reserved slot to ``user_id``'s daily quota.

        Invoked on the worker's terminal-failure path so a failed
        analysis does not permanently consume a slot. Idempotent at the
        zero floor: a release with nothing reserved is a harmless no-op.
        Best-effort — implementations must not raise into the worker's
        cleanup path.
        """
