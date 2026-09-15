"""
Abstract base classes for the job queue.

Separates publish (API side) from consume (worker side).
Implementations can be an in-memory asyncio.Queue (MVP) or RabbitMQ AMQP.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from capelle_platform.models.job import JobMessage


class BasePublisher(ABC):
    """
    Contract for publishing jobs to a named queue.

    The API handler calls publish() after creating an analysis record.
    """

    @abstractmethod
    async def publish(self, queue_name: str, message: JobMessage) -> None:
        """
        Enqueue a job message for asynchronous processing.

        The message must be delivered at-least-once to a consumer.
        """

    @abstractmethod
    async def close(self) -> None:
        """Release publisher resources (connections, channels)."""


class BaseConsumer(ABC):
    """
    Contract for consuming jobs from a named queue.

    The worker pool calls consume() at startup, which blocks until
    the consumer is shut down.
    """

    @abstractmethod
    async def consume(
        self,
        queue_name: str,
        handler: Callable[[JobMessage], Awaitable[None]],
    ) -> None:
        """
        Start consuming messages from the queue.

        Calls handler for each message.  Must support graceful shutdown
        via the stop() method.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Signal the consumer to stop processing and drain in-flight work."""

    @abstractmethod
    async def close(self) -> None:
        """Release consumer resources."""
