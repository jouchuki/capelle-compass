"""
In-memory sliding-window rate limiter.

Designed for the auth endpoints: we keep per-key deques of hit timestamps
and drop anything older than the window. Memory footprint is bounded by
``max_keys`` — we evict the least-recently-seen key when the map grows
past that. Suitable for a single-process deployment; swap for Redis
when we move to multi-worker.
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque
from threading import Lock
from typing import Deque


class SlidingWindowRateLimiter:
    """
    Count hits per key in a rolling time window.

    Thread-safe so FastAPI's async handlers (which may run across
    threads under --workers) can share one instance without races.
    """

    def __init__(
        self,
        *,
        max_hits: int,
        window_seconds: int,
        max_keys: int = 10_000,
    ) -> None:
        if max_hits <= 0 or window_seconds <= 0:
            raise ValueError("max_hits and window_seconds must be positive")
        self._max_hits = max_hits
        self._window = float(window_seconds)
        self._max_keys = max_keys
        self._hits: OrderedDict[str, Deque[float]] = OrderedDict()
        self._lock = Lock()

    def check(self, key: str) -> tuple[bool, int]:
        """
        Record a hit for ``key`` and return ``(allowed, retry_after_seconds)``.

        ``retry_after_seconds`` is 0 when allowed, or the number of
        seconds until the oldest hit in the window falls off when denied.
        """
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            dq = self._hits.get(key)
            if dq is None:
                dq = deque()
                self._hits[key] = dq
                if len(self._hits) > self._max_keys:
                    self._hits.popitem(last=False)
            else:
                self._hits.move_to_end(key)

            while dq and dq[0] < cutoff:
                dq.popleft()

            if len(dq) >= self._max_hits:
                retry_after = max(1, int(dq[0] + self._window - now) + 1)
                return (False, retry_after)

            dq.append(now)
            return (True, 0)
