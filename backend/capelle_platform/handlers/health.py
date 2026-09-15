"""
Health check handler.

Exposes a simple GET /api/health endpoint that returns 200 if the
service is alive.  Used by load balancers, monitoring, and hermes.
"""

from __future__ import annotations

from fastapi import APIRouter

from capelle_platform.observability import get_logger

_logger = get_logger(__name__)


class HealthHandler:
    """
    Handler for liveness and readiness probes.

    Stateless — no dependencies required.  Registered as a FastAPI router.
    """

    def __init__(self) -> None:
        """Initialise the router with health endpoints."""
        self.router = APIRouter(tags=["health"])
        self.router.add_api_route("/api/health", self.health, methods=["GET"])

    async def health(self) -> dict[str, str]:
        """
        Return a simple health status.

        Always returns 200 — if the process is up, it's healthy.
        """
        return {"status": "ok", "service": "capelle-platform"}
