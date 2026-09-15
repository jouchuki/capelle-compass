"""
Pydantic models for telemetry and feedback.

Kept in a dedicated module so the store layer and the handlers can
share identical shapes. The literal ``TelemetryEventType`` and
``FeedbackTopic`` enums exist both for type safety and as the
allowlist for ``POST`` endpoints — clients cannot invent new event
types or feedback topics.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TelemetryEventType = Literal[
    "session_created",
    "session_deleted",
    "message_sent",
    "analysis_completed",
    "analysis_failed",
    "quota_limit_hit",
    "share_link_minted",
    "share_link_opened",
    "share_link_adopted",
    "share_popup_shown",
    "share_popup_dismissed",
    "share_popup_clicked",
    "mailto_opened",
    "onboarding_shown",
    "example_query_clicked",
    "feedback_submitted",
    "report_printed",
    "codex_refresh_failed",
    "user_daily_limit_changed",
]

FeedbackTopic = Literal[
    "bug",
    "suggestion",
    "more_usage",
    "other",
]

FeedbackStatus = Literal["new", "seen", "resolved"]


class UsageEventCreate(BaseModel):
    """
    Client-supplied payload for ``POST /api/telemetry/event``.

    The server fills in ``id``, ``user_id``, and ``ts`` — the client
    must not be trusted to supply those. ``properties`` is an arbitrary
    JSON-serialisable dict; values deeper than one level are allowed
    but should stay small (<4KB serialised) to avoid bloating the
    events table.
    """

    model_config = ConfigDict(extra="forbid")

    event_type: TelemetryEventType = Field(
        ..., description="Identifier chosen from the telemetry allowlist."
    )
    session_id: str | None = Field(
        default=None,
        description="Optional session context; omit for account-scoped events.",
    )
    properties: dict[str, object] = Field(
        default_factory=dict,
        description="Arbitrary JSON-serialisable key-value metadata.",
    )


class UsageEvent(BaseModel):
    """
    Persisted telemetry event as returned by the store.

    ``ts`` is always UTC — timezone-aware. The store stores ISO-8601.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    user_id: str
    event_type: TelemetryEventType
    session_id: str | None = None
    properties: dict[str, object] = Field(default_factory=dict)
    ts: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Server-assigned UTC timestamp.",
    )


class FeedbackCreate(BaseModel):
    """
    Client-supplied payload for ``POST /api/feedback``.

    ``email`` is intentionally NOT accepted from the client — we snapshot
    the authenticated user's email server-side so the inbox can't be
    spoofed. ``content`` is capped at 4000 chars so a hostile client
    cannot wedge the inbox with a multi-megabyte payload.
    """

    model_config = ConfigDict(extra="forbid")

    topic: FeedbackTopic = Field(
        ..., description="Feedback category for triage."
    )
    content: str = Field(
        ..., min_length=1, max_length=4000,
        description="The user's message.",
    )


class FeedbackEntry(BaseModel):
    """
    Persisted feedback row as returned by the store.

    ``email`` is captured at submission time so a later account rename
    cannot obscure who reported the bug.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    user_id: str
    email: str
    topic: FeedbackTopic
    content: str
    status: FeedbackStatus
    created_at: datetime


class TokenUsage(BaseModel):
    """
    Aggregated token usage for a single analysis run.

    Summed across every assistant turn in the ohrs trajectory. Cache
    counters are split so the admin view can tell the difference
    between "first-time context read" (expensive) and "cached context
    replay" (cheap). ``turns`` equals the number of assistant messages
    the agent emitted before terminating.
    """

    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(
        default=None, description="Model identifier from ohrs (_meta.model)."
    )
    turns: int = Field(default=0, ge=0, description="Assistant turns counted.")
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_creation_input_tokens: int = Field(default=0, ge=0)
    cache_read_input_tokens: int = Field(default=0, ge=0)

    @property
    def total_tokens(self) -> int:
        """Grand total across every category — what the ChatGPT Plus
        monthly budget actually debits against."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


class UserQuestion(BaseModel):
    """
    One user-asked question joined with its session + owner metadata.

    The admin dashboard uses this to surface "what people are
    asking" without having to page into each session individually.
    ``session_title`` is the auto-tagged thread title; ``topic`` is
    the 1-3 word auto-assigned topic (may be null).
    """

    model_config = ConfigDict(extra="forbid")

    message_id: str
    session_id: str
    session_title: str
    session_topic: str | None
    user_id: str
    user_email: str
    content: str
    created_at: datetime


class UserRecord(BaseModel):
    """
    Snapshot of one registered user for the admin users table.

    Counts are all-time — the users page exists specifically to show
    every account, including dormant ones. ``is_admin`` is resolved
    server-side against the allowlist so the frontend can flag
    admins without touching settings.

    ``daily_analysis_limit_override`` surfaces the per-user column on
    ``users.daily_analysis_limit``: ``None`` when the row falls back to
    the global default, ``0`` when the admin has granted unlimited,
    or a positive integer cap. Admin-only — never leaked via the
    public endpoints.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: str
    email: str
    created_at: datetime
    is_admin: bool
    sessions: int
    messages_sent: int
    analyses_completed: int
    last_active: datetime | None
    daily_analysis_limit_override: int | None = None


class UserEngagement(BaseModel):
    """
    Per-user activity snapshot used by the admin engagement page.

    All counters are scoped to the query window (``since``); ``first_seen``
    is the account's ``users.created_at``, which may predate the window
    so the admin can distinguish "old quiet user" from "new heavy user".
    ``last_active`` is the most recent telemetry event inside the window;
    ``None`` when the user had no events in the window at all.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: str
    email: str
    sessions_created: int
    messages_sent: int
    analyses_completed: int
    analyses_failed: int
    share_links_minted: int
    share_links_opened: int
    feedback_submitted: int
    quota_limit_hit: int
    first_seen: datetime
    last_active: datetime | None


class UserTokenTotals(BaseModel):
    """
    Per-user token-spend snapshot for the admin "who's heavy" view.

    ``analyses`` is the count of ``analysis_completed`` events in the
    window — useful alongside totals so one big analysis vs. many
    small ones is visible at a glance.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: str
    email: str
    analyses: int
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    total_tokens: int
