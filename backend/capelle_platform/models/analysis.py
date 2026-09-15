"""
Analysis domain models.

An Analysis represents a single agent-driven multi-source investigation.
Status transitions: queued -> running -> completed | failed.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class AnalysisStatus(str, enum.Enum):
    """Lifecycle states for an analysis job."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AnalysisCreate(BaseModel):
    """Payload for submitting a new analysis."""

    query: str = Field(min_length=3, max_length=10000)
    skill: str = Field(default="capelle-analyse")


class AnalysisSummary(BaseModel):
    """Lightweight analysis record for list views (no full result blob)."""

    id: str
    user_id: str
    query: str
    skill: str
    status: AnalysisStatus
    created_at: datetime
    completed_at: datetime | None = None
    error: str | None = None

    model_config: dict[str, object] = {"from_attributes": True}


class Analysis(BaseModel):
    """Full analysis entity including the result payload."""

    id: str
    user_id: str
    query: str
    skill: str
    status: AnalysisStatus
    result: dict[str, Any] | None = None
    result_path: str | None = None
    error: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    completed_at: datetime | None = None

    model_config: dict[str, object] = {"from_attributes": True}
