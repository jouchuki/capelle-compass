"""
Telemetry, quota, and feedback HTTP handler.

Exposes three user-facing routes:
    * ``POST /api/telemetry/event`` — client-side usage events
    * ``GET  /api/usage/me``        — current quota snapshot
    * ``POST /api/feedback``        — submit feedback / support request

All three require an authenticated user — anonymous telemetry is
intentionally not supported; any frontend event that cares about
anonymity would need a separate path designed for it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from capelle_platform.auth.base_authenticator import BaseAuthenticator
from capelle_platform.handlers._base import AuthenticatedHandlerMixin
from capelle_platform.models.telemetry import (
    FeedbackCreate,
    FeedbackEntry,
    UsageEventCreate,
)
from capelle_platform.observability import get_logger
from capelle_platform.quota.base_enforcer import BaseQuotaEnforcer
from capelle_platform.settings import Settings
from capelle_platform.store.base_store import BaseStore
from capelle_platform.telemetry.base_recorder import BaseTelemetryRecorder
from capelle_platform.utils import generate_id

_logger = get_logger(__name__)


class TelemetryHandler(AuthenticatedHandlerMixin):
    """
    HTTP handler for client telemetry, quota snapshots, and feedback.

    Wires together the telemetry recorder, quota enforcer, store, and
    authenticator. No persistence happens here — every IO call is
    delegated through an ABC.
    """

    def __init__(
        self,
        authenticator: BaseAuthenticator,
        store: BaseStore,
        recorder: BaseTelemetryRecorder,
        quota: BaseQuotaEnforcer,
        settings: Settings,
    ) -> None:
        """
        Construct with every collaborator the routes need.

        Args:
            authenticator: Token validator for extracting the user.
            store: Persistence layer for feedback writes.
            recorder: Telemetry sink for usage events.
            quota: Daily-quota enforcer; used for ``/api/usage/me``.
            settings: Cookie name + support email + admin allowlist.
        """
        self._auth = authenticator
        self._store = store
        self._recorder = recorder
        self._quota = quota
        self._settings = settings
        self.router = APIRouter(tags=["telemetry"])
        self.router.add_api_route(
            "/api/telemetry/event",
            self.record_event,
            methods=["POST"],
            status_code=204,
        )
        self.router.add_api_route(
            "/api/usage/me",
            self.get_my_usage,
            methods=["GET"],
        )
        self.router.add_api_route(
            "/api/feedback",
            self.submit_feedback,
            methods=["POST"],
            status_code=201,
        )

    async def record_event(
        self, payload: UsageEventCreate, request: Request
    ) -> None:
        """
        Accept one client-initiated telemetry event.

        Returns 204 No Content — fire-and-forget from the frontend's
        perspective. Internal failures are already swallowed by the
        recorder implementation.
        """
        user = self._get_current_user(request)
        await self._recorder.record(
            user_id=user.id,
            event_type=payload.event_type,
            session_id=payload.session_id,
            properties=payload.properties,
        )

    async def get_my_usage(self, request: Request) -> dict[str, Any]:
        """
        Return the caller's daily-quota snapshot.

        The frontend polls this on page load and after each finished
        analysis so the "X van Y analyses" pill stays fresh without
        having to subscribe to a WebSocket channel.
        """
        user = self._get_current_user(request)
        snapshot = await self._quota.status(user.id)
        return {
            "used": snapshot.used,
            "limit": snapshot.limit,
            "remaining": snapshot.remaining,
            "resets_at": snapshot.resets_at.isoformat(),
        }

    async def submit_feedback(
        self, payload: FeedbackCreate, request: Request
    ) -> dict[str, str]:
        """
        Persist a feedback submission and record the matching event.

        The user's email is snapshot server-side so the inbox cannot
        be spoofed. No SMTP dispatch is performed at the MVP tier;
        the admin dashboard serves as the inbox until we wire mail.
        """
        user = self._get_current_user(request)
        entry = FeedbackEntry(
            id=generate_id(),
            user_id=user.id,
            email=user.email,
            topic=payload.topic,
            content=payload.content.strip(),
            status="new",
            created_at=datetime.now(timezone.utc),
        )
        try:
            await self._store.insert_feedback(entry)
        except Exception as exc:  # noqa: BLE001 — surface as 500 to the user
            _logger.exception("feedback_store_failed", user_id=user.id)
            raise HTTPException(
                status_code=500,
                detail="Kon feedback niet opslaan. Probeer het later opnieuw.",
            ) from exc
        await self._recorder.record(
            user_id=user.id,
            event_type="feedback_submitted",
            properties={"topic": payload.topic, "length": len(entry.content)},
        )
        return {
            "id": entry.id,
            "support_email": self._settings.support_email,
        }
