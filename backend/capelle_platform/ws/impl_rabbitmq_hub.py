"""
Cross-instance WebSocket broadcast hub backed by a RabbitMQ fanout.

Why this file exists
--------------------
The single-process :class:`InMemoryWebSocketHub` is only correct on a
single yuta. As soon as we scale horizontally behind the nginx LB
(masamichi-yaga) the WebSocket connection that a user opened to
``yuta-okkotsu`` may live on one instance while the HTTP POST that
dispatches their analysis job gets load-balanced to ``yuta-maki-zenin``.
When the worker on ``yuta-maki-zenin`` eventually emits
``message_complete`` the in-memory hub on THAT yuta has no record of
the user's socket — it sits on ``yuta-okkotsu``.

The fix is a fanout: every yuta publishes broadcasts into
``capelle.ws`` (an AMQP fanout exchange) and every yuta binds its own
exclusive, auto-delete queue to that exchange. Each instance consumes
its queue and forwards deliveries to ONLY the local WS connections it
owns. Net effect: a ``broadcast_to_user`` call from ``yuta-maki-zenin``
reaches every tab the user has open, wherever those tabs landed.

The exchange is non-durable on purpose: a broadcast missed because a
yuta was down during dispatch is not worth resurrecting — the user
would have already received it via another tab or the analysis
completion page. For durable work (the jobs themselves) we use the
queue exchange, not this one.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aio_pika
from aio_pika.abc import (
    AbstractIncomingMessage,
    AbstractRobustChannel,
    AbstractRobustConnection,
    AbstractRobustExchange,
    AbstractRobustQueue,
)
from fastapi import WebSocket

from capelle_platform.observability import get_logger
from capelle_platform.settings import Settings
from capelle_platform.ws.base_hub import BaseWebSocketHub

_logger = get_logger(__name__)


class RabbitMQWebSocketHub(BaseWebSocketHub):
    """
    Cross-instance WebSocket broadcast hub.

    Uses a fanout exchange to propagate every broadcast to every yuta.
    Each instance still owns its WS connections in-memory; a broadcast
    received from the broker is forwarded only to connections that
    live on THIS yuta. Net effect: ``broadcast_to_user`` from yuta-B
    reaches all of user X's tabs regardless of which yuta they
    connected to.

    Ordering property: because every broadcast (even the one the
    originating yuta "sends to itself") round-trips through the
    broker, all yutas observe broadcasts for a given user in the SAME
    order — the order the fanout delivered them. Short-circuiting to
    local connections would break this and produce subtly different
    transcripts on different tabs.
    """

    # --- Wire-format constants (module-private via class scope) ---
    _EXCHANGE_NAME: str = "capelle.ws"
    _PAYLOAD_USER_KEY: str = "user_id"
    _PAYLOAD_DATA_KEY: str = "data"
    _BROADCAST_ALL_SENTINEL: str = "*"

    # --- Behavioural constants ---
    # Publisher timeout: keep short so the worker pool never stalls
    # progress updates waiting on a flaky broker. Broker-side retries
    # still happen via the robust connection.
    _PUBLISH_TIMEOUT_S: float = 5.0
    # Prefetch: we're doing pure in-memory fan-out in _on_broker_message,
    # so a small window is fine. Too large wastes RAM on broadcast bursts;
    # too small throttles fast workers. 64 is a conservative default.
    _CONSUMER_PREFETCH: int = 64

    def __init__(self, settings: Settings) -> None:
        """
        Initialise hub state. Does NOT connect — that is :meth:`start`.

        Why split: the Builder constructs every component eagerly and
        then runs ``await ws_hub.start()`` inside the FastAPI lifespan.
        Opening a network connection inside ``__init__`` would force
        every caller (including tests) to be in an async context.
        """
        self._settings = settings
        # In-memory connection registry — identical shape to
        # :class:`InMemoryWebSocketHub` because the "how do I deliver
        # to a socket" part of the problem is unchanged; we only
        # added a layer ABOVE it that decides WHICH yuta delivers.
        self._connections: dict[str, list[WebSocket]] = {}
        self._lock = asyncio.Lock()
        # Broker state; all three None until :meth:`start` succeeds.
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._exchange: AbstractRobustExchange | None = None
        self._queue: AbstractRobustQueue | None = None
        self._consumer_task: asyncio.Task[None] | None = None
        # Guards :meth:`start` against double-invocation (lifespan
        # reruns, test fixtures, reconnection races).
        self._start_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Connect to the broker and begin consuming the local fanout queue.

        Idempotent: if the hub is already wired (``_consumer_task`` is
        running) this is a no-op. aio-pika's robust connection handles
        broker restarts transparently; we don't need to tear down and
        rebuild on a simple network blip.
        """
        async with self._start_lock:
            if self._consumer_task is not None and not self._consumer_task.done():
                # Already started; nothing to do.
                return

            # ``connect_robust`` auto-reconnects on broker restart and
            # re-declares anything we declared on its channels.
            self._connection = await aio_pika.connect_robust(
                self._settings.rabbitmq_url
            )
            self._channel = await self._connection.channel()
            await self._channel.set_qos(prefetch_count=self._CONSUMER_PREFETCH)

            # Fanout, non-durable: a broadcast that misses a
            # briefly-offline yuta is NOT replayed — progress events
            # are ephemeral by design. See module docstring.
            self._exchange = await self._channel.declare_exchange(
                self._EXCHANGE_NAME,
                aio_pika.ExchangeType.FANOUT,
                durable=False,
            )
            # Exclusive + auto-delete + server-named: each yuta owns
            # ONE queue bound to the fanout; when the yuta disconnects
            # the broker drops the queue. No cleanup job required.
            self._queue = await self._channel.declare_queue(
                name="",
                exclusive=True,
                auto_delete=True,
                durable=False,
            )
            await self._queue.bind(self._exchange)

            self._consumer_task = asyncio.create_task(
                self._consume_loop(),
                name="capelle-ws-fanout-consumer",
            )
            _logger.info(
                "ws_rabbitmq_started",
                exchange=self._EXCHANGE_NAME,
                queue=self._queue.name,
            )

    async def stop(self) -> None:
        """
        Cancel the consumer task and close the broker connection.

        Safe to call even if :meth:`start` never ran (e.g. a boot that
        aborted before lifespan completed). Each teardown step is
        guarded so a failure in one doesn't mask failures in the
        next.
        """
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                # Cancellation is expected; other exceptions during
                # teardown are logged but should not prevent shutdown
                # of the rest of the lifespan.
                pass
            self._consumer_task = None

        if self._channel is not None:
            try:
                await self._channel.close()
            except Exception as exc:  # noqa: BLE001 — best-effort shutdown
                _logger.warning("ws_rabbitmq_channel_close_failed", error=str(exc))
            self._channel = None

        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception as exc:  # noqa: BLE001 — best-effort shutdown
                _logger.warning(
                    "ws_rabbitmq_connection_close_failed", error=str(exc)
                )
            self._connection = None

        self._exchange = None
        self._queue = None
        _logger.info("ws_rabbitmq_stopped")

    # ------------------------------------------------------------------
    # Local connection registry (shape mirrors InMemoryWebSocketHub)
    # ------------------------------------------------------------------

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        """Accept the handshake and register the connection locally."""
        await websocket.accept()
        async with self._lock:
            self._connections.setdefault(user_id, []).append(websocket)
        _logger.info("ws_connected", user_id=user_id, hub="rabbitmq")

    async def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        """Remove a closed connection. Idempotent."""
        async with self._lock:
            conns = self._connections.get(user_id, [])
            if websocket in conns:
                conns.remove(websocket)
            if not conns:
                self._connections.pop(user_id, None)
        _logger.info("ws_disconnected", user_id=user_id, hub="rabbitmq")

    # ------------------------------------------------------------------
    # Broadcast surface — ALWAYS via the broker
    # ------------------------------------------------------------------

    async def broadcast_to_user(
        self, user_id: str, data: dict[str, Any]
    ) -> None:
        """
        Publish ``data`` to the fanout for ``user_id``.

        We do NOT short-circuit to local connections: the fanout round
        trip delivers the message to every yuta including this one, and
        the local consumer then forwards to local sockets. This keeps
        the global delivery order identical across yutas — crucial so
        two tabs that happen to sit on different backends never see
        events re-ordered relative to each other.

        Broker failures are logged and swallowed. The caller is almost
        always the worker pool emitting a progress event; losing that
        event is preferable to crashing the job.
        """
        await self._publish(user_id, data)

    async def broadcast_all(self, data: dict[str, Any]) -> None:
        """
        Publish ``data`` to every connected user in the fleet.

        Encoded with the wildcard sentinel ``"*"`` in place of a
        user_id; the consumer expands this to every local user on
        receipt. Rarely used (announcements, maintenance banners).
        """
        await self._publish(self._BROADCAST_ALL_SENTINEL, data)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _publish(self, user_id: str, data: dict[str, Any]) -> None:
        """
        Serialise and publish a single fanout message. Never raises.

        Why a single helper: ``broadcast_to_user`` and ``broadcast_all``
        differ only in the sentinel; sharing this method means they
        also share the same timeout + failure-logging behaviour.
        """
        exchange = self._exchange
        if exchange is None:
            # start() was never called, or stop() already ran. Either
            # way a broadcast cannot be serviced; drop with a log so a
            # misconfigured environment is visible in telemetry.
            _logger.warning(
                "ws_broadcast_failed",
                user_id=user_id,
                reason="hub_not_started",
            )
            return

        payload = {
            self._PAYLOAD_USER_KEY: user_id,
            self._PAYLOAD_DATA_KEY: data,
        }
        try:
            body = json.dumps(payload, default=str).encode("utf-8")
            message = aio_pika.Message(
                body=body,
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.NOT_PERSISTENT,
            )
            # Fanout ignores routing key; empty string is conventional.
            await asyncio.wait_for(
                exchange.publish(message, routing_key=""),
                timeout=self._PUBLISH_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 — must not crash caller
            _logger.warning(
                "ws_broadcast_failed",
                user_id=user_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def _consume_loop(self) -> None:
        """
        Read messages from the local fanout queue forever.

        aio-pika's robust channel transparently re-declares the queue
        on reconnect, so we don't need to rebuild the iterator on
        network failure — ``async for`` picks up after recovery.
        Cancellation (during :meth:`stop`) unwinds cleanly; any other
        exception is logged and the loop exits — the robust client
        has already handled transient broker issues upstream.
        """
        queue = self._queue
        if queue is None:
            # Defensive: _consume_loop is only spawned from start(),
            # which always sets _queue before scheduling this task.
            return
        try:
            async with queue.iterator() as it:
                async for message in it:
                    await self._on_broker_message(message)
        except asyncio.CancelledError:
            # Normal shutdown path — let it propagate so the awaiter
            # in stop() sees a clean cancellation.
            raise
        except Exception as exc:  # noqa: BLE001 — log then exit
            _logger.error(
                "ws_rabbitmq_consumer_crashed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def _on_broker_message(
        self, message: AbstractIncomingMessage
    ) -> None:
        """
        Handle one fanout delivery: ack, decode, dispatch locally.

        We ack eagerly (process=False branch of ``message.process``):
        the fanout is non-durable, so a requeue would not help — a
        failed decode should be dropped loudly rather than looped.
        """
        try:
            await message.ack()
        except Exception as exc:  # noqa: BLE001 — best-effort
            _logger.warning("ws_ack_failed", error=str(exc))

        try:
            decoded: Any = json.loads(message.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            _logger.warning("ws_payload_decode_failed", error=str(exc))
            return

        if not isinstance(decoded, dict):
            _logger.warning("ws_payload_shape_invalid", payload_type=type(decoded).__name__)
            return

        user_id = decoded.get(self._PAYLOAD_USER_KEY)
        data = decoded.get(self._PAYLOAD_DATA_KEY)
        if not isinstance(user_id, str) or not isinstance(data, dict):
            _logger.warning(
                "ws_payload_shape_invalid",
                user_id_type=type(user_id).__name__,
                data_type=type(data).__name__,
            )
            return

        await self._broadcast_to_local(user_id, data)

    async def _broadcast_to_local(
        self, user_id: str, data: dict[str, Any]
    ) -> None:
        """
        Fan out ``data`` to LOCAL sockets only.

        Mirrors :class:`InMemoryWebSocketHub` verbatim: serialise once,
        iterate under a snapshot of the connection list, evict dead
        sockets afterwards. The wildcard sentinel ``"*"`` is expanded
        to every currently-connected user.
        """
        payload = json.dumps(data, default=str)

        if user_id == self._BROADCAST_ALL_SENTINEL:
            async with self._lock:
                targets: list[tuple[str, WebSocket]] = [
                    (uid, ws)
                    for uid, conns in self._connections.items()
                    for ws in conns
                ]
        else:
            async with self._lock:
                targets = [(user_id, ws) for ws in self._connections.get(user_id, [])]

        dead: list[tuple[str, WebSocket]] = []
        for uid, ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001 — dead sockets must not block siblings
                dead.append((uid, ws))

        if dead:
            async with self._lock:
                for uid, ws in dead:
                    conns = self._connections.get(uid, [])
                    if ws in conns:
                        conns.remove(ws)
                    if not conns:
                        self._connections.pop(uid, None)
