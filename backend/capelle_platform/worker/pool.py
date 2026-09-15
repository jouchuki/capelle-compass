"""
Async worker pool that consumes jobs and dispatches them to the OhrsExecutor.

Runs N concurrent tasks (controlled by Settings.worker_concurrency),
updates chat messages via the Store, and pushes progress events
through the WebSocket hub.

Thread safety: all state is accessed from the single async event loop.
The pool itself is started as a background task during app lifespan.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any  # noqa: F401 — imported for the telemetry property dict

from capelle_platform.executor.base_executor import BaseExecutor
from capelle_platform.models.message import MessageStatus
from capelle_platform.models.job import JobMessage
from capelle_platform.observability import get_logger
from capelle_platform.observability.exporter import LangfuseExporter
from capelle_platform.observability.logger import TraceContext
from capelle_platform.observability.types import JobMeta
from capelle_platform.quota.base_enforcer import BaseQuotaEnforcer
from capelle_platform.queue.base_queue import BaseConsumer
from capelle_platform.store.base_store import BaseStore
from capelle_platform.telemetry.base_recorder import BaseTelemetryRecorder
from capelle_platform.ws.hub import WebSocketHub

_logger = get_logger(__name__)

# Statuses a message can sit in while work is outstanding. The reaper
# fails any that have aged past the stale threshold.
_NON_TERMINAL_STATUSES: tuple[MessageStatus, ...] = (
    MessageStatus.THINKING,
    MessageStatus.STREAMING,
)

# How often the background reaper sweeps for stuck messages.
_REAPER_INTERVAL_SECONDS: float = 60.0

# Grace multiplier over the job timeout before a non-terminal message is
# considered stuck. A message older than ``job_timeout × this`` cannot
# still be legitimately running, so it is safe to fail it.
_STALE_TIMEOUT_MARGIN: float = 2.0

# Number of times the worker retries the terminal FAILED write before
# giving up — the reaper is the final backstop if every attempt fails.
_FAILED_WRITE_ATTEMPTS: int = 3

# User-facing body written to a message the reaper times out.
_REAPER_FAILURE_CONTENT: str = (
    "Er is een fout opgetreden: de analyse is verlopen voordat een "
    "resultaat kon worden opgeslagen."
)

class AsyncWorkerPool:
    """
    Coordinates job consumption, ohrs execution, message updates, and WS broadcasting.

    The pool bridges the queue layer and the executor layer.
    It owns no business logic — it orchestrates the flow:
    consume message -> mark thinking -> execute ohrs -> update message -> notify.
    """

    def __init__(
        self,
        consumer: BaseConsumer,
        executor: BaseExecutor,
        store: BaseStore,
        ws_hub: WebSocketHub,
        concurrency: int,
        recorder: BaseTelemetryRecorder,
        *,
        quota: BaseQuotaEnforcer | None = None,
        exporter: LangfuseExporter | None = None,
        job_timeout_seconds: int = 600,
        stale_timeout_margin: float = _STALE_TIMEOUT_MARGIN,
        reaper_base_timeout_seconds: int | None = None,
        reaper_interval_seconds: float = _REAPER_INTERVAL_SECONDS,
    ) -> None:
        """
        Construct the worker pool with all required dependencies.

        Args:
            consumer: Queue consumer to pull jobs from.
            executor: Job executor (OhrsExecutor) that spawns the agent.
            store: Persistence layer for message updates.
            ws_hub: WebSocket hub for broadcasting progress to users.
            concurrency: Max concurrent ohrs agents (semaphore size).
            recorder: Telemetry sink for analysis_completed / analysis_failed.
            quota: Quota enforcer used to release a reserved slot when a
                job fails (STORE-6). Optional so older wiring still
                constructs; when ``None`` the release is skipped (a
                failed job then keeps its reserved slot until reset).
            exporter: Optional Langfuse exporter. When not ``None``, each
                completed or failed job ships its trajectory to Langfuse.
                Fail-safe: the exporter swallows its own errors so
                Langfuse being down never breaks a job.
            job_timeout_seconds: Per-job timeout; the reaper derives the
                stale-message threshold from it when ``reaper_base_timeout_seconds``
                is not supplied.
            stale_timeout_margin: Grace multiplier; threshold =
                ``reaper_base_timeout_seconds × stale_timeout_margin``. Must be
                > 1.0 so a still-running job is never reaped. Defaults to the
                module-level ``_STALE_TIMEOUT_MARGIN`` for back-compat.
            reaper_base_timeout_seconds: Base for the stale threshold. Defaults
                to ``job_timeout_seconds`` when ``None``, preserving the old
                behaviour for callers that don't opt in. The builder passes
                ``settings.max_job_timeout_seconds`` so the reaper sits above
                every per-mode ceiling.
            reaper_interval_seconds: How often the reaper loop sweeps. Defaults
                to the module-level ``_REAPER_INTERVAL_SECONDS``.
        """
        self._consumer = consumer
        self._executor = executor
        self._store = store
        self._ws_hub = ws_hub
        self._recorder = recorder
        self._quota = quota
        self._exporter = exporter
        self._job_timeout_seconds = job_timeout_seconds
        self._stale_timeout_margin = stale_timeout_margin
        self._reaper_base_timeout_seconds = (
            reaper_base_timeout_seconds
            if reaper_base_timeout_seconds is not None
            else job_timeout_seconds
        )
        self._reaper_interval_seconds = reaper_interval_seconds
        self._semaphore = asyncio.Semaphore(concurrency)
        self._task: asyncio.Task[None] | None = None
        self._reaper_task: asyncio.Task[None] | None = None

    async def start(self, queue_name: str = "chat_jobs") -> None:
        """
        Launch the pool as a background async task.

        The task runs the consumer loop, which calls _handle_job for each
        message.  Concurrency is bounded by the semaphore. A second
        background task runs the stale-message reaper (STORE-5).
        """
        # Sweep once at startup so messages stranded by a crash before
        # this boot are failed immediately rather than after one interval.
        await self._reap_stale_messages()
        self._task = asyncio.create_task(
            self._consumer.consume(queue_name, self._handle_job)
        )
        self._reaper_task = asyncio.create_task(self._reaper_loop())
        _logger.info("worker_pool_started", queue=queue_name)

    async def stop(self) -> None:
        """Gracefully shut down the pool and wait for in-flight jobs."""
        await self._consumer.stop()
        for task in (self._task, self._reaper_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        _logger.info("worker_pool_stopped")

    async def _reaper_loop(self) -> None:
        """
        Periodically fail messages stuck in a non-terminal state.

        Runs until cancelled at shutdown. Each sweep is wrapped so a
        transient store error never kills the loop. The interval is
        configurable via ``reaper_interval_seconds`` at construction time
        (defaults to ``_REAPER_INTERVAL_SECONDS``).
        """
        while True:
            await asyncio.sleep(self._reaper_interval_seconds)
            try:
                await self._reap_stale_messages()
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("reaper_sweep_failed")

    async def _reap_stale_messages(self) -> None:
        """
        Mark over-aged THINKING/STREAMING messages FAILED and notify.

        A message older than ``job_timeout × _STALE_TIMEOUT_MARGIN`` can
        no longer be legitimately running (a published-but-never-consumed
        job, or one whose worker died), so it is swept into a terminal
        state and the owning sockets are told. The store flips the rows
        atomically and returns the affected ids.
        """
        threshold = timedelta(
            seconds=self._reaper_base_timeout_seconds * self._stale_timeout_margin
        )
        cutoff = datetime.now(timezone.utc) - threshold
        stale = await self._store.fail_stale_messages(
            non_terminal=_NON_TERMINAL_STATUSES,
            older_than=cutoff,
            failure_content=_REAPER_FAILURE_CONTENT,
        )
        for message_id, user_id in stale:
            await self._ws_hub.broadcast_to_user(
                user_id,
                {
                    "type": "message_failed",
                    "message_id": message_id,
                    "error": "timeout",
                },
            )

    async def progress_callback(
        self, user_id: str, message_id: str, event: dict[str, Any]
    ) -> None:
        """
        Broadcast a tool progress event to the user's WebSocket connections.

        Called by the OhrsExecutor while tailing the trajectory file.
        """
        await self._ws_hub.broadcast_to_user(user_id, {
            "type": "progress",
            "message_id": message_id,
            **event,
        })

    async def _handle_job(self, message: JobMessage) -> None:
        """
        Process a single job message with concurrency limiting.

        Acquires the semaphore before executing, so at most N ohrs
        agents run simultaneously.
        """
        async with self._semaphore:
            await self._execute_job(message)

    async def _execute_job(self, job: JobMessage) -> None:
        """
        Full lifecycle of a single job: thinking -> execute ohrs -> complete/failed.

        Updates the assistant message and broadcasts WebSocket events
        at each transition.

        Idempotency (STORE-1): the job is claimed with a conditional
        ``THINKING -> STREAMING`` transition before any work runs. Under
        at-least-once queue delivery a redelivered message finds the
        claim already taken (rowcount 0) and returns immediately — no
        second agent run, no duplicate usage accounting, no overwritten COMPLETE
        message.
        """
        TraceContext.set(job.trace_id)
        message_id = job.message_id
        user_id = job.user_id

        claimed = await self._store.claim_message_for_processing(
            message_id,
            expected_status=MessageStatus.THINKING,
            claimed_status=MessageStatus.STREAMING,
        )
        if not claimed:
            _logger.info(
                "job_already_claimed",
                message_id=message_id,
                trace_id=job.trace_id,
            )
            return

        try:
            await self._ws_hub.broadcast_to_user(user_id, {
                "type": "status",
                "message_id": message_id,
                "status": "streaming",
            })

            result: dict[str, Any] = await self._executor.execute(job)

            content = result.get("content", "Analyse afgerond.")
            await self._store.update_message(
                message_id,
                content=content,
                status=MessageStatus.COMPLETE,
                metadata=result,
            )
            await self._ws_hub.broadcast_to_user(user_id, {
                "type": "message_complete",
                "message_id": message_id,
                "content": content,
                "metadata": result,
            })
            _logger.info("job_completed", message_id=message_id)

            # Only successful completions count against the daily quota;
            # the recorder is the single source of truth both here and
            # for the quota enforcer.
            sections = result.get("sections")
            section_count = (
                len(sections) if isinstance(sections, list) else 0
            )
            # Token usage from the ohrs trajectory is aggregated by the
            # executor and attached to the result. Land it on the same
            # event so we can attribute model usage directly to the
            # user who triggered it.
            usage = result.get("usage") if isinstance(result, dict) else None
            event_properties: dict[str, Any] = {
                "message_id": message_id,
                "section_count": section_count,
            }
            if isinstance(usage, dict):
                for key in (
                    "model",
                    "turns",
                    "input_tokens",
                    "output_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                    "total_tokens",
                ):
                    if key in usage and usage[key] is not None:
                        event_properties[key] = usage[key]
            await self._recorder.record(
                user_id=user_id,
                event_type="analysis_completed",
                session_id=job.session_id,
                properties=event_properties,
            )
            if self._exporter is not None:
                blocks = (result.get("analysis") or {}).get("blocks")
                await self._exporter.export_job(JobMeta(
                    message_id=message_id,
                    mode=str(job.mode),
                    user_id=user_id,
                    session_id=job.session_id,
                    query=job.query,
                    status="completed",
                    block_count=len(blocks) if isinstance(blocks, list) else 0,
                    section_count=section_count,
                ))

            # Flag Codex refresh failures separately so the admin dashboard
            # can surface a "system status" strip with recent occurrences.
            # The job itself may have succeeded (ohrs falls back to the
            # existing access token), but the next refresh cycle will
            # 401 again until someone re-syncs tokens from ~/.codex/auth.json.
            if isinstance(result, dict) and result.get("codex_refresh_failed"):
                await self._recorder.record(
                    user_id=user_id,
                    event_type="codex_refresh_failed",
                    session_id=job.session_id,
                    properties={"message_id": message_id},
                )

        except Exception as exc:
            error_msg = str(exc)[:500]
            _logger.exception("job_failed", message_id=message_id)

            # Guaranteed terminal write (STORE-5): persist FAILED with a
            # bounded retry BEFORE the WS broadcast, so a transient store
            # blip cannot leave the message stuck non-terminal. If every
            # attempt fails the periodic reaper is the final backstop.
            await self._write_terminal_failed(message_id, error_msg)

            # Release the reserved quota slot (STORE-6) — a failed
            # analysis must not permanently consume the user's daily
            # budget. Best-effort; the enforcer swallows its own errors.
            if self._quota is not None:
                await self._quota.release(user_id)

            await self._ws_hub.broadcast_to_user(user_id, {
                "type": "message_failed",
                "message_id": message_id,
                "error": error_msg,
            })
            await self._recorder.record(
                user_id=user_id,
                event_type="analysis_failed",
                session_id=job.session_id,
                properties={
                    "message_id": message_id,
                    "error": error_msg[:200],
                },
            )
            if self._exporter is not None:
                await self._exporter.export_job(JobMeta(
                    message_id=message_id,
                    mode=str(job.mode),
                    user_id=user_id,
                    session_id=job.session_id,
                    query=job.query,
                    status="failed",
                ))

    async def _write_terminal_failed(
        self, message_id: str, error_msg: str
    ) -> None:
        """
        Persist the FAILED terminal state with a bounded retry.

        Retried because the FAILED write is the one store call that, if
        dropped, strands a message non-terminal forever. The reaper still
        backstops a total failure, but a few retries here resolve the
        common transient-blip case immediately.
        """
        content = f"Er is een fout opgetreden: {error_msg}"
        for attempt in range(1, _FAILED_WRITE_ATTEMPTS + 1):
            try:
                await self._store.update_message(
                    message_id,
                    content=content,
                    status=MessageStatus.FAILED,
                )
                return
            except Exception:
                _logger.exception(
                    "message_update_failed",
                    message_id=message_id,
                    attempt=attempt,
                )
                if attempt < _FAILED_WRITE_ATTEMPTS:
                    await asyncio.sleep(0.5 * attempt)
