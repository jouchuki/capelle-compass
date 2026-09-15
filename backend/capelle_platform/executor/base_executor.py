"""
Abstract base class for job execution.

An Executor takes a JobMessage and produces a result dict.
Implementations may spawn CLI subprocesses, call HTTP APIs, or
invoke agent frameworks directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from capelle_platform.models.job import JobMessage


class BaseExecutor(ABC):
    """
    Contract for executing an analysis job.

    The worker pool delegates actual work to an Executor instance.
    Execution is expected to be long-running (seconds to minutes).
    """

    @abstractmethod
    async def execute(
        self, job: JobMessage
    ) -> dict[str, Any]:
        """
        Run the analysis described by the job message.

        Returns the result payload as a dict (matches AnalysisResult schema).
        Raises ExecutionError on unrecoverable failure.
        """

    @abstractmethod
    async def cancel(self, analysis_id: str) -> None:
        """
        Attempt to cancel a running job.

        Best-effort — the subprocess or agent may not honour cancellation.
        """
