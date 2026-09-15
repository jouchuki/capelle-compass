"""
Admin analytics handler — gated by the email allowlist.

Every route answers 403 unless the caller's authenticated email is in
``Settings.admin_emails``. The shapes here are intentionally tailored
for the ``/admin`` dashboard: aggregates come pre-bucketed so the
frontend can feed chart.js directly without re-aggregating in the
browser.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, field_validator

from capelle_platform.auth.base_authenticator import BaseAuthenticator
from capelle_platform.handlers._base import AuthenticatedHandlerMixin
from capelle_platform.models.telemetry import (
    FeedbackStatus,
    FeedbackTopic,
    TelemetryEventType,
)
from capelle_platform.observability import get_logger
from capelle_platform.settings import Settings
from capelle_platform.store.base_store import BaseStore
from capelle_platform.telemetry.base_recorder import BaseTelemetryRecorder

_logger = get_logger(__name__)

_DEFAULT_WINDOW_DAYS = 30
_MAX_WINDOW_DAYS = 180

# Hard ceiling on a per-user daily-analysis override. 1000 is far
# above any plausible human workflow; anything larger almost always
# indicates a typo or scripted abuse, so we reject it at the API
# boundary rather than persist a silent mistake.
_MAX_DAILY_LIMIT_OVERRIDE = 1000


class DailyLimitUpdate(BaseModel):
    """
    Admin request body for ``POST /api/admin/users/{user_id}/daily-limit``.

    ``limit`` carries the override semantics documented on the quota
    enforcer:

    * ``None`` — clear the override; user falls back to the global
      ``CAPELLE_DAILY_ANALYSIS_LIMIT``.
    * ``0``    — unlimited; the enforcer never rejects the user.
    * positive integer up to ``_MAX_DAILY_LIMIT_OVERRIDE`` — hard cap.

    Negative integers and integers above the ceiling are rejected
    with a 422 so the admin UI can surface a clean validation error.
    """

    model_config = ConfigDict(extra="forbid")

    limit: int | None

    @field_validator("limit")
    @classmethod
    def _validate_range(cls, value: int | None) -> int | None:
        """Reject negative caps and caps above the safety ceiling."""
        if value is None:
            return value
        if value < 0:
            raise ValueError(
                "limit must be null, 0 (unlimited), or a positive integer"
            )
        if value > _MAX_DAILY_LIMIT_OVERRIDE:
            raise ValueError(
                f"limit must be at most {_MAX_DAILY_LIMIT_OVERRIDE}"
            )
        return value


class AdminHandler(AuthenticatedHandlerMixin):
    """
    Read-only analytics endpoints for the internal dashboard.

    Never writes — all mutation goes through the user-facing handlers
    so admin actions stay auditable as the application of ordinary
    operations (e.g. marking a feedback entry "seen" is still just a
    future ``PATCH /api/feedback/{id}`` from the dashboard app).
    """

    def __init__(
        self,
        authenticator: BaseAuthenticator,
        store: BaseStore,
        settings: Settings,
        recorder: BaseTelemetryRecorder,
    ) -> None:
        """
        Construct with auth + store + settings + telemetry recorder.

        The recorder is only used by write endpoints (the daily-limit
        setter); read endpoints on this router do not emit telemetry
        so that the admin dashboard does not spam its own analytics.
        """
        self._auth = authenticator
        self._store = store
        self._settings = settings
        self._recorder = recorder
        self.router = APIRouter(prefix="/api/admin", tags=["admin"])
        self.router.add_api_route("/check", self.check, methods=["GET"])
        self.router.add_api_route(
            "/stats/overview", self.overview, methods=["GET"]
        )
        self.router.add_api_route(
            "/stats/timeseries", self.timeseries, methods=["GET"]
        )
        self.router.add_api_route("/feedback", self.list_feedback, methods=["GET"])
        self.router.add_api_route(
            "/events", self.list_events, methods=["GET"]
        )
        self.router.add_api_route(
            "/stats/tokens", self.token_spend, methods=["GET"]
        )
        self.router.add_api_route(
            "/stats/engagement", self.engagement, methods=["GET"]
        )
        self.router.add_api_route(
            "/users", self.list_all_users, methods=["GET"]
        )
        self.router.add_api_route(
            "/questions", self.list_questions, methods=["GET"]
        )
        self.router.add_api_route(
            "/users/{user_id}/daily-limit",
            self.set_user_daily_limit,
            methods=["POST"],
            status_code=status.HTTP_204_NO_CONTENT,
        )

    async def check(self, request: Request) -> dict[str, Any]:
        """
        Trivial probe for ``/admin`` route-gating on the frontend.

        Returns ``{"is_admin": true, "email": ...}`` when the cookie/token
        maps to an allowlisted email, 403 otherwise. Cheap — the frontend
        can call it once on page load.
        """
        user = self._get_current_user(request)
        self._require_admin(user)
        return {"is_admin": True, "email": user.email}

    async def overview(self, request: Request) -> dict[str, Any]:
        """
        Top-line numbers for the dashboard hero card.

        Windows are fixed at "last 24 hours" and "last 30 days" so the
        view is deterministic; custom windows live on the timeseries
        endpoint.
        """
        user = self._get_current_user(request)
        self._require_admin(user)

        now = datetime.now(timezone.utc)
        day_ago = now - timedelta(days=1)
        month_ago = now - timedelta(days=_DEFAULT_WINDOW_DAYS)

        accounts_total = await self._store.count_users_created_since(
            datetime(1970, 1, 1, tzinfo=timezone.utc)
        )
        accounts_last_24h = await self._store.count_users_created_since(day_ago)
        accounts_last_30d = await self._store.count_users_created_since(month_ago)

        analyses_day_series = await self._store.count_events_grouped_by_day(
            "analysis_completed", days=_DEFAULT_WINDOW_DAYS
        )
        messages_day_series = await self._store.count_events_grouped_by_day(
            "message_sent", days=_DEFAULT_WINDOW_DAYS
        )
        shares_minted = await self._store.count_events_grouped_by_day(
            "share_link_minted", days=_DEFAULT_WINDOW_DAYS
        )
        shares_opened = await self._store.count_events_grouped_by_day(
            "share_link_opened", days=_DEFAULT_WINDOW_DAYS
        )
        shares_adopted = await self._store.count_events_grouped_by_day(
            "share_link_adopted", days=_DEFAULT_WINDOW_DAYS
        )
        quota_hits = await self._store.count_events_grouped_by_day(
            "quota_limit_hit", days=_DEFAULT_WINDOW_DAYS
        )
        feedback_backlog = await self._store.list_feedback(
            status="new", limit=1
        )
        tokens_series = await self._store.sum_tokens_grouped_by_day(
            days=_DEFAULT_WINDOW_DAYS
        )
        total_tokens_30d = sum(value for _, value in tokens_series)
        # Per-day input/output/cache split powers the stacked Overzicht
        # chart. Single-value tokens_per_day is kept for backward
        # compatibility — older frontends fall back to it.
        tokens_breakdown = await self._store.sum_tokens_breakdown_grouped_by_day(
            days=_DEFAULT_WINDOW_DAYS
        )
        # Codex refresh failures bucketed by hour across the last 24h
        # so the dashboard can render a spark chart. A single failure
        # five days ago isn't actionable; the recent window is.
        codex_failures_by_hour = await self._store.count_events_grouped_by_hour(
            "codex_refresh_failed", hours=24
        )

        response: dict[str, Any] = {
            "accounts": {
                "total": accounts_total,
                "last_24h": accounts_last_24h,
                "last_30d": accounts_last_30d,
            },
            "series_days": _DEFAULT_WINDOW_DAYS,
            "series": {
                "analyses_completed": [
                    {"day": d, "count": c} for d, c in analyses_day_series
                ],
                "messages_sent": [
                    {"day": d, "count": c} for d, c in messages_day_series
                ],
                "share_link_minted": [
                    {"day": d, "count": c} for d, c in shares_minted
                ],
                "share_link_opened": [
                    {"day": d, "count": c} for d, c in shares_opened
                ],
                "share_link_adopted": [
                    {"day": d, "count": c} for d, c in shares_adopted
                ],
                "quota_limit_hit": [
                    {"day": d, "count": c} for d, c in quota_hits
                ],
                "tokens_per_day": [
                    {"day": d, "count": c} for d, c in tokens_series
                ],
                "tokens_by_type_per_day": [
                    {"day": d, "input": inp, "output": out, "cache": cache}
                    for d, inp, out, cache in tokens_breakdown
                ],
                "codex_refresh_per_hour": [
                    {"hour": h, "count": c} for h, c in codex_failures_by_hour
                ],
            },
            "funnels": {
                "share": {
                    "minted": sum(c for _, c in shares_minted),
                    "opened": sum(c for _, c in shares_opened),
                    "adopted": sum(c for _, c in shares_adopted),
                },
            },
            "tokens": {
                "total_last_30d": total_tokens_30d,
            },
            "feedback_new": len(feedback_backlog),
        }
        return response

    async def timeseries(
        self,
        request: Request,
        event_type: TelemetryEventType = Query(..., description="Event key"),
        days: int = Query(default=_DEFAULT_WINDOW_DAYS, ge=1, le=_MAX_WINDOW_DAYS),
    ) -> dict[str, Any]:
        """
        Per-day bucketed counts for any single event type.

        Caller controls window length. Days are returned as
        ``YYYY-MM-DD`` strings anchored to UTC; the dashboard renders
        them in the browser's locale.
        """
        user = self._get_current_user(request)
        self._require_admin(user)
        series = await self._store.count_events_grouped_by_day(
            event_type, days=days
        )
        return {
            "event_type": event_type,
            "days": days,
            "series": [{"day": d, "count": c} for d, c in series],
        }

    async def list_feedback(
        self,
        request: Request,
        status: Literal["new", "seen", "resolved"] | None = Query(default=None),
        topic: Literal["bug", "suggestion", "more_usage", "other"] | None = Query(default=None),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        """Return the admin inbox — feedback rows newest-first."""
        user = self._get_current_user(request)
        self._require_admin(user)
        typed_status: FeedbackStatus | None = status  # type: ignore[assignment]
        typed_topic: FeedbackTopic | None = topic  # type: ignore[assignment]
        # Fetch one extra row to cheaply detect whether a "next page"
        # exists without a separate COUNT(*) roundtrip.
        rows = await self._store.list_feedback(
            status=typed_status, topic=typed_topic, offset=offset, limit=limit + 1
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        return {
            "items": [row.model_dump(mode="json") for row in page],
            "count": len(page),
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
        }

    async def token_spend(
        self,
        request: Request,
        days: int = Query(default=_DEFAULT_WINDOW_DAYS, ge=1, le=_MAX_WINDOW_DAYS),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        """
        Token-spend leaderboard for the admin dashboard.

        Returns ``items`` sorted by total tokens descending so the
        heaviest users float to the top. The frontend consumes this
        alongside the per-day spark series already in ``/stats/overview``.
        """
        user = self._get_current_user(request)
        self._require_admin(user)
        since = datetime.now(timezone.utc) - timedelta(days=days)
        totals = await self._store.sum_tokens_by_user_since(
            since, offset=offset, limit=limit + 1
        )
        has_more = len(totals) > limit
        page = totals[:limit]
        # grand_total reflects the current page only; a cross-page total
        # would require a separate aggregate and isn't load-bearing for
        # the dashboard.
        grand_total = sum(t.total_tokens for t in page)
        return {
            "days": days,
            "grand_total": grand_total,
            "items": [t.model_dump() for t in page],
            "count": len(page),
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
        }

    async def engagement(
        self,
        request: Request,
        days: int = Query(default=_DEFAULT_WINDOW_DAYS, ge=1, le=_MAX_WINDOW_DAYS),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=2000),
    ) -> dict[str, Any]:
        """
        Per-user engagement leaderboard for the admin engagement page.

        Returns the full set of event-based counters joined on the
        users table so the dashboard can render one row per active
        user. Users with zero events in the window are omitted — this
        endpoint intentionally answers "who's engaging", not "who has
        an account".
        """
        user = self._get_current_user(request)
        self._require_admin(user)
        since = datetime.now(timezone.utc) - timedelta(days=days)
        rows = await self._store.aggregate_engagement_since(
            since, offset=offset, limit=limit + 1
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        return {
            "days": days,
            "count": len(page),
            "items": [r.model_dump(mode="json") for r in page],
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
        }

    async def list_all_users(
        self,
        request: Request,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        """
        Paginated list of every registered user.

        Includes dormant accounts (no events recorded) — the engagement
        endpoint already answers "who's active", this one answers
        "who exists". ``is_admin`` is resolved against the allowlist
        server-side so the frontend can render a badge without leaking
        the allowlist itself.
        """
        user = self._get_current_user(request)
        self._require_admin(user)
        rows = await self._store.list_users_paginated(
            offset=offset,
            limit=limit + 1,
            admin_emails=self._settings.admin_email_set,
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        return {
            "items": [r.model_dump(mode="json") for r in page],
            "count": len(page),
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
        }

    async def list_questions(
        self,
        request: Request,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        """
        Paginated list of recent user-submitted questions.

        Each row carries the question content + who asked + which
        session it belongs to, newest first. Soft-deleted sessions
        are filtered out at the store layer so admins only see live
        threads.
        """
        user = self._get_current_user(request)
        self._require_admin(user)
        rows = await self._store.list_recent_questions(
            offset=offset, limit=limit + 1
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        return {
            "items": [r.model_dump(mode="json") for r in page],
            "count": len(page),
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
        }

    async def list_events(
        self,
        request: Request,
        event_type: TelemetryEventType | None = Query(default=None),
        user_id: str | None = Query(default=None),
        days: int = Query(default=_DEFAULT_WINDOW_DAYS, ge=1, le=_MAX_WINDOW_DAYS),
        limit: int = Query(default=500, ge=1, le=5000),
    ) -> dict[str, Any]:
        """
        Raw event list with optional filters — newest first.

        Used by the dashboard's "recent activity" table and by ad-hoc
        debugging. Time window is enforced server-side so a curious
        admin cannot accidentally pull the entire events table.
        """
        user = self._get_current_user(request)
        self._require_admin(user)
        since = datetime.now(timezone.utc) - timedelta(days=days)
        rows = await self._store.list_usage_events(
            event_type=event_type,
            user_id=user_id,
            since=since,
            limit=limit,
        )
        return {
            "items": [row.model_dump(mode="json") for row in rows],
            "count": len(rows),
        }

    async def set_user_daily_limit(
        self,
        user_id: str,
        payload: DailyLimitUpdate,
        request: Request,
    ) -> Response:
        """
        Admin: set ``users.daily_analysis_limit`` for ``user_id``.

        Accepts ``{"limit": null | 0 | positive int <= 1000}``. See
        :class:`DailyLimitUpdate` for the full contract; Pydantic
        rejects out-of-range integers with a 422 before we ever
        reach the store.

        * ``null`` clears the override — user returns to the global
          default from ``Settings.daily_analysis_limit``.
        * ``0`` marks the user unlimited — the quota enforcer never
          raises for them.
        * positive integers set a hard cap.

        Returns ``204 No Content`` on success. Records a
        ``user_daily_limit_changed`` telemetry event against the
        **admin** performing the change with
        ``{target_user_id, new_limit}`` in properties so the audit
        trail shows both sides of the transaction.

        404s if ``user_id`` does not exist so an admin typo cannot
        silently persist a row no user can ever look up.
        """
        admin_user = self._get_current_user(request)
        self._require_admin(admin_user)

        target = await self._store.get_user_by_id(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="User not found")

        await self._store.set_user_daily_limit(user_id, payload.limit)
        await self._recorder.record(
            user_id=admin_user.id,
            event_type="user_daily_limit_changed",
            properties={
                "target_user_id": user_id,
                "new_limit": payload.limit,
            },
        )
        _logger.info(
            "admin_daily_limit_updated",
            admin_id=admin_user.id,
            target_user_id=user_id,
            new_limit=payload.limit,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
