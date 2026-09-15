"""
RabbitMQ implementation of the BasePublisher / BaseConsumer ABCs.

Why RabbitMQ at all
-------------------
The in-memory ``impl_memory.py`` is fine when a single yuta-* backend
owns both the HTTP handler and the worker pool.  As soon as we horizontally
scale to N yuta instances behind a load balancer, the API yuta that
accepts a request is not necessarily the yuta that should run the ohrs
agent — and every yuta has its own private asyncio.Queue, so jobs would
strand on whichever process happened to receive the POST.

This module lets any yuta publish to — and any yuta consume from — a
single shared AMQP queue on the ``kiyotaka-ijichi`` broker, giving us a
cooperative work-stealing pool across the fleet.

Topology (declared idempotently on every connect)
-------------------------------------------------
- Exchange ``capelle.jobs``            direct, durable
- Queue    ``chat_jobs``               durable, DLX-routed, bound ``chat_jobs``
- DLX      ``capelle.jobs.dlx``        direct, durable
- DLQ      ``chat_jobs.dead``          durable, bound ``chat_jobs``

Durability + persistent delivery + manual ack together guarantee that
an accepted job survives both a broker restart and a yuta crash.  DLX
catches terminal handler failures so ops can inspect them instead of
the message being redelivered forever.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any, Final

import aio_pika
from aio_pika.abc import (
    AbstractIncomingMessage,
    AbstractRobustChannel,
    AbstractRobustConnection,
    AbstractRobustExchange,
    AbstractRobustQueue,
)

from capelle_platform.models.job import JobMessage
from capelle_platform.observability import get_logger
from capelle_platform.queue.base_queue import BaseConsumer, BasePublisher
from capelle_platform.settings import Settings

_logger = get_logger(__name__)

# --- Topology constants --------------------------------------------------
# All broker-side names live here so they are grep-able and cannot be
# typo'd apart between publisher and consumer — both sides must agree on
# every string or messages end up in the wrong queue.

# Primary direct exchange for all job traffic. Direct (not topic) because
# we key purely on queue-name equality — no wildcard fan-out needed.
_JOBS_EXCHANGE_NAME: Final[str] = "capelle.jobs"

# Dead-letter exchange. Terminal failures (handler raised) are routed
# here via the x-dead-letter-exchange argument on the main queue, with
# the original routing key preserved so ops can trace where it came from.
_DLX_EXCHANGE_NAME: Final[str] = "capelle.jobs.dlx"

# Suffix appended to every main queue to derive its DLQ name.  We build
# the DLQ name at declare-time as ``f"{queue_name}{_DEAD_QUEUE_SUFFIX}"``
# so adding a second traffic class (e.g. ``analysis_jobs``) does not need
# a second constant — the convention is self-describing.
_DEAD_QUEUE_SUFFIX: Final[str] = ".dead"

# AMQP header key used to carry the JobMessage.trace_id onto the
# broker-visible envelope.  Matches the header name the observability
# stack already greps for in yuta logs.
_TRACE_ID_HEADER: Final[str] = "x-trace-id"


class _RabbitMQBase:
    """
    Shared lazy-connect plumbing for both Publisher and Consumer.

    Why lazy?  Builder wires these at import time of ``main.py`` — way
    before the event loop is running — and unit tests import the module
    without a broker at all.  Opening the AMQP socket in ``__init__``
    would explode under both.  Instead we defer it to the first publish
    / consume call, guarded by an ``asyncio.Lock`` so concurrent first
    calls do not race to open two sockets.
    """

    def __init__(self, settings: Settings) -> None:
        """
        Stash settings; do NOT touch the broker yet.

        The Builder constructs both classes synchronously during FastAPI
        app setup — no event loop, no I/O allowed.
        """
        self._settings = settings
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._exchange: AbstractRobustExchange | None = None
        # Guards the first-use connection+topology declaration.  Once
        # ``_connection`` is set this lock is only taken on the (rare)
        # close path; steady-state publish/consume is lock-free.
        self._connect_lock = asyncio.Lock()

    async def _ensure_connected(self) -> AbstractRobustExchange:
        """
        Open the connection + declare topology on first call, return
        the cached exchange on every subsequent call.

        Idempotent: declaring an exchange/queue that already exists with
        matching args is a no-op on the broker, so reconnects after a
        transient network blip safely re-run this path.
        """
        if self._exchange is not None:
            return self._exchange

        async with self._connect_lock:
            # Double-checked locking: another coroutine may have
            # finished the declare between our check and the lock.
            if self._exchange is not None:
                return self._exchange

            _logger.info(
                "rabbitmq_connecting",
                url_host=self._redacted_host(self._settings.rabbitmq_url),
            )
            # ``connect_robust`` returns a RobustConnection that
            # auto-reconnects on broker / network failures.  We trust
            # it to heal itself — consumers see redelivered messages,
            # publishers see a brief exception then success on retry.
            connection = await aio_pika.connect_robust(
                self._settings.rabbitmq_url,
            )
            # One channel per publisher/consumer instance is plenty —
            # aio-pika multiplexes internally, and sharing channels
            # across publishers would couple their QoS / confirm state.
            channel = await connection.channel()
            # Per-consumer prefetch = worker_concurrency so a single
            # yuta never pulls more messages than it can actually run
            # in parallel ohrs agents.  Publisher sets this too
            # (harmlessly) so both sides use one code path.
            await channel.set_qos(
                prefetch_count=self._settings.worker_concurrency,
            )

            # --- Declare exchanges (idempotent) ---
            # Main jobs exchange: direct + durable so it survives a
            # broker restart and only delivers to exactly-matching
            # routing keys (i.e. queue names).
            jobs_exchange = await channel.declare_exchange(
                _JOBS_EXCHANGE_NAME,
                aio_pika.ExchangeType.DIRECT,
                durable=True,
            )
            # DLX: same shape, different name.  Terminal failures go
            # here so ops can bind an inspection queue without polluting
            # the hot path.
            await channel.declare_exchange(
                _DLX_EXCHANGE_NAME,
                aio_pika.ExchangeType.DIRECT,
                durable=True,
            )

            self._connection = connection
            self._channel = channel
            self._exchange = jobs_exchange
            _logger.info("rabbitmq_connected")
            return jobs_exchange

    async def _declare_queue(
        self,
        queue_name: str,
    ) -> AbstractRobustQueue:
        """
        Declare + bind the main queue AND its dead-letter companion.

        Called from both publisher (so publishes to a never-consumed
        queue still land somewhere durable) and consumer (so the consume
        side is self-sufficient if it boots first).  Idempotent on the
        broker so calling it from both sides is safe.
        """
        exchange = await self._ensure_connected()
        assert self._channel is not None  # set by _ensure_connected

        dead_queue_name = f"{queue_name}{_DEAD_QUEUE_SUFFIX}"

        # --- Dead-letter queue first ---
        # Declared before the main queue so by the time the main queue
        # references the DLX, a concrete destination queue already
        # exists.  Not strictly required by AMQP (DLX routing is lazy),
        # but it makes the startup logs linear and easy to read.
        dead_queue = await self._channel.declare_queue(
            dead_queue_name,
            durable=True,
        )
        await dead_queue.bind(
            _DLX_EXCHANGE_NAME,
            routing_key=queue_name,
        )

        # --- Main queue ---
        # ``x-dead-letter-exchange`` tells the broker to forward any
        # nack(requeue=False) or TTL-expired message to the DLX with
        # the original routing key preserved.
        main_queue = await self._channel.declare_queue(
            queue_name,
            durable=True,
            arguments={
                "x-dead-letter-exchange": _DLX_EXCHANGE_NAME,
            },
        )
        await main_queue.bind(exchange, routing_key=queue_name)
        return main_queue

    async def _close_connection(self) -> None:
        """
        Close channel + connection if they were ever opened.

        Safe to call multiple times and safe to call when the lazy
        connection never happened — both just short-circuit.
        """
        async with self._connect_lock:
            if self._channel is not None and not self._channel.is_closed:
                await self._channel.close()
            if (
                self._connection is not None
                and not self._connection.is_closed
            ):
                await self._connection.close()
            self._channel = None
            self._connection = None
            self._exchange = None

    @staticmethod
    def _redacted_host(url: str) -> str:
        """
        Extract just ``host:port`` from the AMQP URL for log lines.

        Never log the full URL — it carries the broker password.  This
        helper is a static method so both classes can use it without a
        free function at module scope (keeps the public module API at
        exactly two names).
        """
        if "@" in url:
            tail = url.split("@", 1)[1]
        else:
            tail = url
        # Strip any trailing ``/vhost`` so we log only the endpoint.
        return tail.split("/", 1)[0]


class RabbitMQPublisher(_RabbitMQBase, BasePublisher):
    """
    Publishes ``JobMessage`` envelopes to ``capelle.jobs`` with
    persistent delivery.

    The ``queue_name`` argument doubles as the routing key — we use a
    direct exchange with queue-name equality so the publisher never has
    to know which physical queue (``chat_jobs`` today, possibly more
    tomorrow) the message ends up on.
    """

    async def publish(self, queue_name: str, message: JobMessage) -> None:
        """
        Serialise the message to JSON and publish it durably.

        Contract:
        - Declares the target queue before the first publish so
          messages cannot be discarded by ``mandatory`` routing.
        - ``DeliveryMode.PERSISTENT`` + durable queue + durable
          exchange = broker restart cannot lose unprocessed jobs.
        - ``trace_id`` is copied into an AMQP header so broker-side
          introspection (rabbitmqctl list_queues, management UI) can
          correlate envelopes back to yuta log lines.
        - On any failure we log + re-raise; the chat handler turns that
          into a 5xx.  A publish we cannot confirm is a job we cannot
          promise to run.
        """
        try:
            exchange = await self._ensure_connected()
            await self._declare_queue(queue_name)
            body = message.model_dump_json().encode("utf-8")
            amqp_message = aio_pika.Message(
                body=body,
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=message.message_id,
                headers={_TRACE_ID_HEADER: message.trace_id},
            )
            await exchange.publish(
                amqp_message,
                routing_key=queue_name,
            )
            _logger.info(
                "job_published",
                message_id=message.message_id,
                queue=queue_name,
                trace_id=message.trace_id,
            )
        except Exception as exc:
            _logger.exception(
                "queue_publish_failed",
                message_id=message.message_id,
                queue=queue_name,
                trace_id=message.trace_id,
                error=type(exc).__name__,
            )
            raise

    async def close(self) -> None:
        """Tear down the AMQP connection on application shutdown."""
        await self._close_connection()


class RabbitMQConsumer(_RabbitMQBase, BaseConsumer):
    """
    Consumes ``JobMessage`` envelopes from the shared AMQP queue.

    Work-stealing semantics: every yuta in the fleet runs its own
    ``RabbitMQConsumer`` against the same ``chat_jobs`` queue, so the
    broker round-robins deliveries among them.  Prefetch ensures a
    single yuta cannot monopolise the queue — once it has
    ``worker_concurrency`` unacked messages, the broker stops sending
    it more until it acks one.
    """

    def __init__(self, settings: Settings) -> None:
        """
        Stash settings and init the stop signal.

        The actual consumer tag / queue object come into existence in
        ``consume`` so tests can import this class without a broker.
        """
        super().__init__(settings)
        self._stop_event = asyncio.Event()
        self._queue: AbstractRobustQueue | None = None

    async def consume(
        self,
        queue_name: str,
        handler: Callable[[JobMessage], Awaitable[None]],
    ) -> None:
        """
        Start consuming; block until ``stop()`` is called.

        Flow per message:
        1. Deserialise bytes -> ``JobMessage`` (Pydantic validates shape).
        2. ``await handler(message)`` — executes the job.
        3. On success: ``message.ack()``.
        4. On handler exception: log ``job_handler_failed`` + ``nack``
           with ``requeue=False`` so the broker routes it to the DLX
           instead of redelivering forever.
        5. On malformed bytes (JSON/Pydantic error): same DLX path —
           a message we cannot even parse will never become processable
           by retry, so replaying it is pure noise.

        Broker disconnects are handled by ``aio_pika.connect_robust``;
        we log a warning at reconnect and let the library heal.
        """
        self._stop_event.clear()
        queue = await self._declare_queue(queue_name)
        self._queue = queue
        _logger.info(
            "consumer_started",
            queue=queue_name,
            prefetch=self._settings.worker_concurrency,
        )

        active_tasks: set[asyncio.Task[None]] = set()

        async with queue.iterator() as message_iter:
            async for amqp_message in message_iter:
                if self._stop_event.is_set():
                    # Return the in-flight message to the queue — it is
                    # NOT our failure, we are just shutting down, so
                    # requeue=True keeps the work alive for another yuta.
                    await amqp_message.nack(requeue=True)
                    break

                # Do not await inline here. RabbitMQ prefetch already caps
                # unacked deliveries to worker_concurrency; dispatching a
                # task per delivery lets one yuta actually run that many jobs
                # concurrently instead of reserving N messages and processing
                # them serially.
                task = asyncio.create_task(self._dispatch_one(amqp_message, handler))
                active_tasks.add(task)
                task.add_done_callback(active_tasks.discard)

        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)

        _logger.info("consumer_stopped", queue=queue_name)

    @staticmethod
    async def _dispatch_one(
        amqp_message: AbstractIncomingMessage,
        handler: Callable[[JobMessage], Awaitable[None]],
    ) -> None:
        """
        Handle a single delivery: parse, invoke handler, ack or DLQ.

        Static because it shares no state with the consumer instance —
        keeping it on the class (rather than a module-level function)
        honours the "two classes, no free functions" rule while still
        letting the code read top-down inside ``consume``.

        The one ``Any`` in this module lives here: ``json.loads`` truly
        returns ``Any`` and typing the AMQP boundary more strictly
        would be dishonest.
        """
        # message_id is present on every envelope we publish — fall
        # back to a constant so the log line is never empty.
        message_id = amqp_message.message_id or "<missing>"
        try:
            payload: Any = json.loads(amqp_message.body.decode("utf-8"))
            job = JobMessage.model_validate(payload)
        except Exception as exc:
            _logger.exception(
                "job_payload_invalid",
                message_id=message_id,
                error=type(exc).__name__,
                detail=str(exc)[:200],
            )
            await amqp_message.nack(requeue=False)
            return

        try:
            await handler(job)
        except Exception as exc:
            _logger.exception(
                "job_handler_failed",
                message_id=job.message_id,
                trace_id=job.trace_id,
                error=type(exc).__name__,
                detail=str(exc)[:200],
            )
            await amqp_message.nack(requeue=False)
            return

        await amqp_message.ack()

    async def stop(self) -> None:
        """
        Signal the consume loop to exit after the current iteration.

        Idempotent: setting an already-set Event is a no-op, so double
        shutdown signals from the Builder's lifespan do not error.
        """
        self._stop_event.set()
        _logger.info("consumer_stopping")

    async def close(self) -> None:
        """
        Stop the loop (if still running) and close the AMQP connection.

        Called from the FastAPI lifespan on process shutdown.  Order
        matters: set the stop event first so the iterator cleanly
        returns before we pull the channel out from under it.
        """
        await self.stop()
        await self._close_connection()
