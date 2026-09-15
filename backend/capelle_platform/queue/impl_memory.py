"""
In-memory asyncio.Queue implementation of the job queue.

Drop-in replacement for RabbitMQ during local development and MVP.
Same ABC interface — swap to RabbitMQ by changing one line in the Builder.

Thread safety: asyncio.Queue is coroutine-safe.  No shared mutable state
beyond the queue itself, which is accessed only from async contexts.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from capelle_platform.models.job import JobMessage
from capelle_platform.observability import get_logger
from capelle_platform.queue.base_queue import BaseConsumer, BasePublisher

_logger = get_logger(__name__)


class _SharedQueue:
    """
    Lazy-loaded singleton holding the actual asyncio.Queue.

    Ensures publisher and consumer share the same queue instance
    within a single process.  Heavy resource (the queue) is initialised
    once and reused across the lifetime of the process.
    """

    _instance: _SharedQueue | None = None
    _queue: asyncio.Queue[JobMessage] | None = None

    @classmethod
    def get(cls) -> asyncio.Queue[JobMessage]:
        """Return the singleton queue, creating it on first access."""
        if cls._instance is None:
            cls._instance = cls()
            cls._queue = asyncio.Queue()
        assert cls._queue is not None
        return cls._queue


class MemoryPublisher(BasePublisher):
    """
    Publishes job messages to an in-memory asyncio.Queue.

    The queue_name parameter is accepted for interface compatibility
    but ignored — all messages go to the single shared queue.
    """

    async def publish(self, queue_name: str, message: JobMessage) -> None:
        """Enqueue a job message for the in-process worker pool."""
        queue = _SharedQueue.get()
        await queue.put(message)
        _logger.info(
            "job_published",
            message_id=message.message_id,
            queue=queue_name,
        )

    async def close(self) -> None:
        """No-op for in-memory queue."""


class MemoryConsumer(BaseConsumer):
    """
    Consumes job messages from the shared in-memory asyncio.Queue.

    Runs a perpetual get-loop until stop() is called.
    """

    def __init__(self) -> None:
        """Initialise the consumer with a stop signal + live task set."""
        self._running = False
        # Strong references to in-flight dispatch tasks. Without this set
        # asyncio only weakly references the tasks and may garbage-collect
        # a job mid-flight; the done-callback discards each one when it
        # settles.
        self._active_tasks: set[asyncio.Task[None]] = set()

    async def consume(
        self,
        queue_name: str,
        handler: Callable[[JobMessage], Awaitable[None]],
    ) -> None:
        """
        Start the consume loop, dispatching each message concurrently.

        Blocks until stop() is called.  Uses a 1-second timeout on
        queue.get() so the stop signal is checked regularly.

        Each message is handed to ``asyncio.create_task`` rather than
        awaited inline (STORE-7). Awaiting inline silently pinned the
        worker to a concurrency of 1 regardless of ``worker_concurrency``;
        the pool's own semaphore still bounds true parallelism, mirroring
        the RabbitMQ consumer's task-per-delivery model.
        """
        self._running = True
        queue = _SharedQueue.get()
        _logger.info("consumer_started", queue=queue_name)

        while self._running:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            task = asyncio.create_task(self._dispatch_one(message, handler))
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)

        # Drain in-flight work so a graceful stop does not abandon jobs
        # that were already dispatched.
        if self._active_tasks:
            await asyncio.gather(*self._active_tasks, return_exceptions=True)

    @staticmethod
    async def _dispatch_one(
        message: JobMessage,
        handler: Callable[[JobMessage], Awaitable[None]],
    ) -> None:
        """Invoke the handler for one message, logging any failure."""
        try:
            await handler(message)
        except Exception:
            _logger.exception(
                "job_handler_failed", message_id=message.message_id
            )

    async def stop(self) -> None:
        """Signal the consume loop to exit after the current iteration."""
        self._running = False
        _logger.info("consumer_stopping")

    async def close(self) -> None:
        """No-op for in-memory queue."""
        await self.stop()
