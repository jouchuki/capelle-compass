"""
Chat message domain models.

A ChatMessage represents a single turn in a conversation —
user input, assistant response, system notice, or tool progress event.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class MessageRole(str, enum.Enum):
    """Who produced this message."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL_PROGRESS = "tool_progress"


class MessageStatus(str, enum.Enum):
    """Lifecycle state for assistant messages."""

    COMPLETE = "complete"
    THINKING = "thinking"
    STREAMING = "streaming"
    FAILED = "failed"


class ChatMessageCreate(BaseModel):
    """Payload for sending a new user message."""

    content: str = Field(min_length=1, max_length=2000)
    # Optional node_id for node-scoped follow-up messages (Task 5).
    # When set, the executor injects the referenced node's local subgraph as
    # context so the agent can deepen, verify, or branch from that finding.
    # Absent (None) for regular messages — backward compatible.
    node_id: str | None = None
    # Multi-node scope: deepen several findings at once. Supersedes node_id
    # when both are present. Additive — None for regular messages.
    node_ids: list[str] | None = None
    # Optional grounding text from the document the user is working in (the
    # Office add-in sends the open document's body). When present the executor
    # injects it as a clearly-delimited context section BEFORE the question so
    # the agent grounds in it without citing it as a source. Absent (None) for
    # ordinary web-chat messages — backward compatible. Capped to keep the
    # prompt bounded; the add-in truncates client-side too.
    document_context: str | None = Field(default=None, max_length=20000)


class ChatMessage(BaseModel):
    """Persisted chat message entity."""

    id: str
    session_id: str
    role: MessageRole
    content: str
    status: MessageStatus = MessageStatus.COMPLETE
    metadata: dict[str, Any] | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    model_config: dict[str, object] = {"from_attributes": True}
