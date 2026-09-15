"""
Client-IP extraction that honors ``X-Forwarded-For`` when behind a
trusted reverse proxy (the ``masamichi-yaga`` nginx LB wedge).

Without this, every request's peer is the LB, so the rate limiter
buckets every user into one global ``ip:<lb>`` slot — a single brute
forcer would block everyone.
"""

from __future__ import annotations

from fastapi import Request


class ClientIpResolver:
    """
    Resolve the real client IP from a FastAPI ``Request``.

    The resolver is constructed with the set of "trusted proxies" —
    peer addresses whose ``X-Forwarded-For`` header we believe. When
    the immediate peer is in that set, we pop the **rightmost** entry
    from ``X-Forwarded-For`` and use it as the client IP; a malicious
    upstream cannot spoof a trusted address because only our own LB is
    in the trusted set.

    When the peer is NOT trusted (e.g. a rogue container bypassing the
    LB), we ignore the header entirely and fall back to ``request.client.host``.
    """

    def __init__(self, trusted_proxies: frozenset[str]) -> None:
        """
        Store the lowercase-normalised trusted-proxy set.

        Comparison is done against ``request.client.host`` which is the
        peer's IP as uvicorn saw it. Hostnames are resolved server-side
        if the LB connects by IP; for tailnet hostnames the ASGI peer
        is always an IP so we store IPs in the set.
        """
        self._trusted = frozenset(p.strip() for p in trusted_proxies if p.strip())

    def resolve(self, request: Request) -> str:
        """
        Return the best-guess client IP for ``request``.

        Falls back to the string ``"unknown"`` when neither the peer
        nor a trusted forwarded entry is available, so rate-limit keys
        never become ``None``.
        """
        peer = request.client.host if request.client else ""
        if peer and peer in self._trusted:
            forwarded = request.headers.get("x-forwarded-for", "")
            if forwarded:
                # X-Forwarded-For is a comma-separated list of
                # client, proxy1, proxy2 — the leftmost entry is the
                # original client. Strip whitespace on each hop.
                candidates = [p.strip() for p in forwarded.split(",") if p.strip()]
                if candidates:
                    return candidates[0]
        return peer or "unknown"
