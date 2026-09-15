"""
Job message models for the internal work queue.

A JobMessage is the envelope that travels from the chat handler
through the queue to the worker pool.  It carries everything the
executor needs to spawn an ohrs agent without reaching back to the store.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel

from capelle_platform.models.mode import DEFAULT_MODE, Mode


class JobStatus(str, enum.Enum):
    """Lifecycle states scoped to the queue layer."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobMessage(BaseModel):
    """Serialisable payload published to the work queue."""

    session_id: str
    message_id: str
    user_id: str
    query: str
    skill: str
    trace_id: str
    # Analysis mode picked by the user on the landing-page chooser. Travels
    # with the job to the executor, which uses it to select the ModeConfig
    # (workspace dir + system prompt + skill set). Defaults to the legacy
    # groeikern mode so pre-mode jobs replayed from a queue snapshot still
    # execute the way they were intended.
    mode: Mode = DEFAULT_MODE
    # Prior conversation context for follow-up turns. Each entry has the
    # shape ``{"role": "user"|"assistant", "content": "..."}``. Empty for
    # the first message in a session; populated with truncated prior
    # messages for every subsequent turn so the executor can build a
    # context-aware prompt without another round-trip to the store.
    history: list[dict[str, str]] = []
    # Optional node_id for node-scoped follow-ups (Task 5). When set the
    # executor fetches the referenced node from the session graph and
    # prepends a compact FOCUS NODE block to the prompt so the agent can
    # deepen, verify, or branch from that specific finding. None for
    # ordinary (non-node-scoped) messages — fully backward compatible.
    node_id: str | None = None
    # Multi-node scope: one FOCUS NODE block per id (capped, in order,
    # unknown/pruned ids skipped). Supersedes node_id when both are set.
    node_ids: list[str] | None = None
    # Optional grounding text from the document the user is working in (sent by
    # the Office add-in). When non-empty the executor injects it as a
    # delimited DOCUMENT section before the user's question so the agent uses
    # it as context without citing it as a source. None for ordinary messages.
    document_context: str | None = None
