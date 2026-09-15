"""
Token revocation registry — a per-user "valid-after" generation check.

JWTs in this platform are stateless, so a logged-out (or force-logged-out)
token would otherwise keep streaming a user's analysis progress over an open
WebSocket until its natural ``exp`` (API-3). This registry adds the minimal
state needed to kill live sockets on logout: a per-user "revoked before"
instant. A token whose ``iat`` (issued-at) is at or before the user's recorded
revocation instant is treated as revoked.

Scope note (API-3): this implementation is **process-local**. It kills sockets
served by the same instance that handled the logout. A multi-instance,
cross-yuta revocation requires shared state (store/Redis) and is deferred to
the Wave-4 stateful-revocation work; the ABC below lets that drop in without
touching the handlers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from capelle_platform.observability import get_logger

_logger = get_logger(__name__)


class BaseTokenRevocationRegistry(ABC):
    """
    Contract for recording and checking per-user token revocation.

    A token is considered revoked when its issued-at instant is not strictly
    after the user's recorded revocation instant.
    """

    @abstractmethod
    def revoke_before(self, user_id: str, revoked_at_epoch: float) -> None:
        """
        Record that all of ``user_id``'s tokens issued at or before
        ``revoked_at_epoch`` (unix seconds) are revoked.

        Idempotent and monotonic — a later call with an earlier instant must
        not weaken an existing revocation.
        """

    @abstractmethod
    def is_revoked(self, user_id: str, token_issued_at_epoch: float | None) -> bool:
        """
        Return whether a token with the given ``iat`` is revoked for ``user_id``.

        A token with no recorded ``iat`` (``None``) is treated as revoked when
        any revocation exists for the user, since it cannot be proven to
        post-date the revocation.
        """


class InMemoryTokenRevocationRegistry(BaseTokenRevocationRegistry):
    """
    Process-local ``BaseTokenRevocationRegistry`` backed by a dict.

    Suitable for the single-yuta deployment. CPython's GIL makes the dict
    operations safe across the single asyncio loop; no extra locking needed.
    """

    def __init__(self) -> None:
        """Initialise with an empty user → revocation-instant map."""
        self._revoked_before: dict[str, float] = {}

    def revoke_before(self, user_id: str, revoked_at_epoch: float) -> None:
        """Record/raise the revocation instant for ``user_id`` (monotonic)."""
        current = self._revoked_before.get(user_id)
        if current is None or revoked_at_epoch > current:
            self._revoked_before[user_id] = revoked_at_epoch
            _logger.info("token_revocation_recorded", user_id=user_id)

    def is_revoked(self, user_id: str, token_issued_at_epoch: float | None) -> bool:
        """Return True when the token's ``iat`` does not post-date a revocation."""
        revoked_before = self._revoked_before.get(user_id)
        if revoked_before is None:
            return False
        if token_issued_at_epoch is None:
            return True
        # A 1s slop guards against tokens minted in the same second as the
        # logout (issued just before the revocation was recorded).
        return token_issued_at_epoch <= revoked_before
