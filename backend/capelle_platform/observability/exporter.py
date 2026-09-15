from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from capelle_platform.observability.logger import get_logger
from capelle_platform.observability.mapper import build_ingestion_batch
from capelle_platform.observability.types import JobMeta

_logger = get_logger(__name__)


class _Client(Protocol):
    """Protocol for the Langfuse ingestion client.

    Anything with a ``send_batch`` coroutine satisfies this contract,
    which lets tests inject a fake without importing ``LangfuseClient``.
    """

    async def send_batch(self, events: list[dict]) -> bool: ...


class LangfuseExporter:
    """Locate a job's trajectory, map it, and ship it to Langfuse.

    Reconstructs the trajectory path as ``jobs_dir / message_id /
    trajectory.jsonl`` (matching the executor's ``job_dir`` layout), so it
    works for both completed and failed jobs without the executor passing
    anything back. Gated by ``enabled``; every failure is swallowed so
    Langfuse being down or missing trajectories can never break a job.
    """

    def __init__(self, client: _Client, *, jobs_dir: Path, enabled: bool) -> None:
        """Construct a LangfuseExporter.

        Args:
            client: A :class:`LangfuseClient` (or compatible fake).
            jobs_dir: Root directory where per-job subdirectories live (i.e.
                ``settings.ohrs_jobs_dir``). The trajectory for a given job is
                at ``jobs_dir / message_id / trajectory.jsonl``.
            enabled: Whether to export. When ``False``, ``export_job`` is a
                no-op so disabling via settings has zero overhead.
        """
        self._client = client
        self._jobs_dir = Path(jobs_dir)
        self._enabled = enabled

    async def export_job(self, job: JobMeta) -> None:
        """Map and export a single completed or failed job to Langfuse.

        Silently skips when disabled or when the trajectory file is absent
        (e.g. the job died before the executor wrote anything). Any other
        exception is caught and logged as a warning — observability must
        never raise into the worker.

        Args:
            job: Per-job metadata providing the trace identity and context.
        """
        if not self._enabled:
            return
        try:
            path = self._jobs_dir / job.message_id / "trajectory.jsonl"
            if not path.exists():
                return
            rows = [
                json.loads(line)
                for line in path.read_text().splitlines()
                if line.strip()
            ]
            # Stamp the real export wall-clock so the trace is dated correctly;
            # OHRS writes the trajectory's own system timestamp wrong/future-dated.
            stamped = (
                job
                if job.timestamp
                else replace(job, timestamp=datetime.now(timezone.utc).isoformat())
            )
            events = build_ingestion_batch(rows, stamped)
            await self._client.send_batch(events)
        except Exception as exc:  # noqa: BLE001 — never break a job
            _logger.warning(
                "langfuse_export_failed",
                message_id=job.message_id,
                error=str(exc),
            )
