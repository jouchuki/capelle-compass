"""
Cross-instance elicitation registry backed by a RabbitMQ fanout.

Why this file exists
--------------------
:class:`InMemoryElicitationRegistry` keeps each pending question's
``Future`` in one process's memory. That is only correct on a single
yuta. Behind the nginx LB (round-robin, no affinity) the agent's
``capelle-ask`` runs on whichever yuta consumed the job from RabbitMQ
and registers the ``Future`` there — but the user's *answer* HTTP POST
is load-balanced independently and frequently lands on a DIFFERENT
yuta, whose registry has no such question. The answer is dropped
(``elicitation_resolve_unknown``) and the agent blocks until the
elicitation timeout, then proceeds with an empty answer.

The fix mirrors :class:`RabbitMQWebSocketHub`: an answer is published to
the ``capelle.elicitation`` fanout exchange; every yuta binds its own
exclusive, auto-delete queue and consumes. The yuta that actually owns
the pending ``Future`` resolves it locally (re-checking ownership); the
others no-op. Net effect: an answer accepted on yuta-B resolves a
question registered on yuta-A.

The owning yuta still holds the ``Future`` in memory (it must — that is
where the awaiting ``capelle-ask`` request is suspended); RabbitMQ only
carries the *answer event* to it. The exchange is non-durable on
purpose: a missed answer is no worse than today's timeout, and the
durable job queue is unaffected.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import aio_pika
from aio_pika.abc import (
    AbstractIncomingMessage,
    AbstractRobustChannel,
    AbstractRobustConnection,
    AbstractRobustExchange,
    AbstractRobustQueue,
)

from capelle_platform.elicitation.base_registry import BaseElicitationRegistry
from capelle_platform.observability import get_logger
from capelle_platform.settings import Settings

_logger = get_logger(__name__)


@dataclass
class _PendingQuestion:
    """A pending question's Future plus the id of the user that owns it."""

    future: asyncio.Future[str]
    owner_user_id: str


class RabbitMQElicitationRegistry(BaseElicitationRegistry):
    """
    Cross-instance elicitation registry.

    Pending ``Future`` objects live in THIS process (only the yuta running
    the job can suspend on them). Answers are fanned out over RabbitMQ so
    the owning yuta resolves its ``Future`` regardless of which yuta the
    answer POST landed on.
    """

    # --- Wire-format constants ---
    _EXCHANGE_NAME: str = "capelle.elicitation"
    _KEY_QID: str = "question_id"
    _KEY_ANSWER: str = "answer"
    _KEY_USER: str = "requesting_user_id"

    # --- Behavioural constants ---
    _PUBLISH_TIMEOUT_S: float = 5.0
    _CONSUMER_PREFETCH: int = 32

    def __init__(self, settings: Settings) -> None:
        """Initialise state. Does NOT connect — that is :meth:`start`."""
        self._settings = settings
        self._pending: dict[str, _PendingQuestion] = {}
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._exchange: AbstractRobustExchange | None = None
        self._queue: AbstractRobustQueue | None = None
        self._consumer_task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect to the broker and begin consuming the answer fanout.

        Idempotent: a second call while already wired is a no-op. The robust
        connection re-declares the exchange/queue transparently on reconnect.
        """
        async with self._start_lock:
            if self._consumer_task is not None and not self._consumer_task.done():
                return
            self._connection = await aio_pika.connect_robust(
                self._settings.rabbitmq_url
            )
            self._channel = await self._connection.channel()
            await self._channel.set_qos(prefetch_count=self._CONSUMER_PREFETCH)
            self._exchange = await self._channel.declare_exchange(
                self._EXCHANGE_NAME,
                aio_pika.ExchangeType.FANOUT,
                durable=False,
            )
            self._queue = await self._channel.declare_queue(
                name="",
                exclusive=True,
                auto_delete=True,
                durable=False,
            )
            await self._queue.bind(self._exchange)
            self._consumer_task = asyncio.create_task(
                self._consume_loop(),
                name="capelle-elicitation-fanout-consumer",
            )
            _logger.info(
                "elicitation_rabbitmq_started",
                exchange=self._EXCHANGE_NAME,
                queue=self._queue.name,
            )

    async def stop(self) -> None:
        """Cancel the consumer and close the broker connection. Best-effort."""
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._consumer_task = None
        if self._channel is not None:
            try:
                await self._channel.close()
            except Exception as exc:  # noqa: BLE001 — best-effort shutdown
                _logger.warning(
                    "elicitation_rabbitmq_channel_close_failed", error=str(exc)
                )
            self._channel = None
        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception as exc:  # noqa: BLE001 — best-effort shutdown
                _logger.warning(
                    "elicitation_rabbitmq_connection_close_failed", error=str(exc)
                )
            self._connection = None
        self._exchange = None
        self._queue = None
        _logger.info("elicitation_rabbitmq_stopped")

    # ------------------------------------------------------------------
    # Registry surface (BaseElicitationRegistry)
    # ------------------------------------------------------------------

    async def register(
        self, question_id: str, owner_user_id: str
    ) -> asyncio.Future[str]:
        """Create and store a local ``Future`` bound to its owning user.

        The ``Future`` is intentionally process-local: the awaiting
        ``capelle-ask`` request is suspended in THIS process, so only this
        process can deliver its result.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending[question_id] = _PendingQuestion(
            future=future, owner_user_id=owner_user_id
        )
        _logger.info(
            "elicitation_registered",
            question_id=question_id,
            pending_count=len(self._pending),
        )
        return future

    def resolve(
        self, question_id: str, answer: str, requesting_user_id: str
    ) -> bool:
        """Deliver ``answer`` to ``question_id``, across yutas if needed.

        Local-first: if this yuta owns the question, resolve immediately
        with the ownership check. Otherwise publish the answer to the
        fanout so the owning yuta resolves it (the owner re-checks
        ownership on receipt), and report the answer as accepted/routed.

        Returns ``True`` when resolved locally or routed to the fleet;
        ``False`` only when the question IS local but ownership fails or it
        is already done.
        """
        if question_id in self._pending:
            return self._resolve_local(question_id, answer, requesting_user_id)
        # Not ours — fan the answer out so the owning yuta resolves it.
        self._schedule_publish(question_id, answer, requesting_user_id)
        return True

    def discard(self, question_id: str) -> None:
        """Remove the local entry for ``question_id``. Idempotent."""
        if self._pending.pop(question_id, None) is not None:
            _logger.info("elicitation_discarded", question_id=question_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_local(
        self, question_id: str, answer: str, requesting_user_id: str
    ) -> bool:
        """Resolve a locally-owned question, enforcing the API-1 owner check."""
        entry = self._pending.get(question_id)
        if entry is None:
            _logger.info("elicitation_resolve_unknown", question_id=question_id)
            return False
        if entry.owner_user_id != requesting_user_id:
            _logger.warning(
                "elicitation_resolve_owner_mismatch",
                question_id=question_id,
                requesting_user_id=requesting_user_id,
            )
            return False
        if entry.future.done():
            _logger.info("elicitation_resolve_already_done", question_id=question_id)
            return False
        entry.future.set_result(answer)
        _logger.info(
            "elicitation_resolved",
            question_id=question_id,
            answer_length=len(answer),
        )
        return True

    def _schedule_publish(
        self, question_id: str, answer: str, requesting_user_id: str
    ) -> None:
        """Fire-and-forget publish of an answer to the fanout (never raises)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _logger.warning(
                "elicitation_publish_no_loop", question_id=question_id
            )
            return
        loop.create_task(
            self._publish(question_id, answer, requesting_user_id),
            name=f"elicitation-publish-{question_id}",
        )

    async def _publish(
        self, question_id: str, answer: str, requesting_user_id: str
    ) -> None:
        """Publish one answer to the fanout. Never raises."""
        exchange = self._exchange
        if exchange is None:
            _logger.warning(
                "elicitation_publish_failed",
                question_id=question_id,
                reason="registry_not_started",
            )
            return
        payload = {
            self._KEY_QID: question_id,
            self._KEY_ANSWER: answer,
            self._KEY_USER: requesting_user_id,
        }
        try:
            message = aio_pika.Message(
                body=json.dumps(payload).encode("utf-8"),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.NOT_PERSISTENT,
            )
            await asyncio.wait_for(
                exchange.publish(message, routing_key=""),
                timeout=self._PUBLISH_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 — must not crash the caller
            _logger.warning(
                "elicitation_publish_failed",
                question_id=question_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def _consume_loop(self) -> None:
        """Read fanned-out answers forever and apply them to local futures."""
        queue = self._queue
        if queue is None:
            return
        try:
            async with queue.iterator() as it:
                async for message in it:
                    await self._on_broker_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — log then exit
            _logger.error(
                "elicitation_rabbitmq_consumer_crashed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def _on_broker_message(self, message: AbstractIncomingMessage) -> None:
        """Decode one answer delivery and resolve it if we own the question."""
        try:
            await message.ack()
        except Exception as exc:  # noqa: BLE001 — best-effort
            _logger.warning("elicitation_ack_failed", error=str(exc))
        try:
            decoded = json.loads(message.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            _logger.warning("elicitation_payload_decode_failed", error=str(exc))
            return
        if not isinstance(decoded, dict):
            return
        qid = decoded.get(self._KEY_QID)
        answer = decoded.get(self._KEY_ANSWER)
        user = decoded.get(self._KEY_USER)
        if (
            not isinstance(qid, str)
            or not isinstance(answer, str)
            or not isinstance(user, str)
        ):
            _logger.warning("elicitation_payload_shape_invalid")
            return
        # Only the owning yuta has this qid; everyone else ignores it.
        if qid in self._pending:
            self._resolve_local(qid, answer, user)
