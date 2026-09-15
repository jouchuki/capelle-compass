from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JobMeta:
    """Per-job metadata for a Langfuse trace.

    ``message_id`` doubles as the Langfuse trace id and is also how the
    exporter locates the trajectory file (``ohrs_jobs_dir / message_id``).
    """

    message_id: str
    mode: str
    user_id: str
    session_id: str
    query: str
    status: str  # "completed" | "failed"
    block_count: int = 0
    section_count: int = 0
    # Real wall-clock time (ISO-8601) for the trace. The exporter stamps this
    # at export; the mapper prefers it over the trajectory's own system
    # timestamp, which OHRS has been observed to write wrong/future-dated.
    timestamp: str | None = None
