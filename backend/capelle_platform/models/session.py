"""
Chat session domain models.

A ChatSession groups related messages into a conversation thread.
Each user can have multiple sessions, each with its own context.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from capelle_platform.models.mode import DEFAULT_MODE, Mode
from capelle_platform.web.host import DEFAULT_DOMAIN, Domain


class ChatSessionCreate(BaseModel):
    """Payload for creating a new chat session.

    The optional ``mode`` field is set by the landing-page chooser; if absent
    we fall back to ``DEFAULT_MODE`` so existing API clients keep working
    without code changes.
    """

    title: str = Field(default="Nieuwe sessie", max_length=200)
    mode: Mode = DEFAULT_MODE


class ChatSession(BaseModel):
    """
    Persisted chat session entity.

    ``deleted_at`` is a soft-delete marker: when set, the row is hidden
    from user-facing listings but preserved in the DB for audit and
    restore. The store's ``list_sessions`` already filters deleted
    rows; ``get_session`` does not — the handler decides whether to
    serve a tombstoned session for specific flows (fork view, etc.).

    ``mode`` records the analysis mode chosen at session creation. Existing
    rows from before this column existed default to ``DEFAULT_MODE`` via
    the SQLite migration / Postgres ``ALTER TABLE IF NOT EXISTS``.

    """

    id: str
    user_id: str
    title: str
    topic: str | None = None
    mode: Mode = DEFAULT_MODE
    domain: Domain = DEFAULT_DOMAIN
    deleted_at: datetime | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    model_config: dict[str, object] = {"from_attributes": True}
