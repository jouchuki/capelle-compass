"""
Store-backed implementation of ``BaseTelemetryRecorder``.

Writes events through the injected ``BaseStore``. Catches every
exception at the sink boundary so handlers can call ``record`` inside
``try/except``-free hot paths.
"""

from __future__ import annotations

from datetime import datetime, timezone

from capelle_platform.models.telemetry import TelemetryEventType, UsageEvent
from capelle_platform.observability import get_logger
from capelle_platform.store.base_store import BaseStore
from capelle_platform.telemetry.base_recorder import BaseTelemetryRecorder
from capelle_platform.utils import generate_id

_logger = get_logger(__name__)


class StoreBackedTelemetryRecorder(BaseTelemetryRecorder):
    """
    Persist telemetry events to the same store as chat messages.

    Deliberately boring: one row per event, no batching. Fine for MVP
    — the event rate is bounded by human interaction. Switch to a
    batched writer when DAU crosses a few hundred.
    """

    def __init__(self, store: BaseStore) -> None:
        """
        Construct with the shared persistence store.

        The recorder keeps no mutable state so a single instance can be
        shared across workers without additional synchronisation.
        """
        self._store = store

    async def record(
        self,
        *,
        user_id: str,
        event_type: TelemetryEventType,
        session_id: str | None = None,
        properties: dict[str, object] | None = None,
    ) -> None:
        """Assemble the UsageEvent and hand it to the store."""
        event = UsageEvent(
            id=generate_id(),
            user_id=user_id,
            event_type=event_type,
            session_id=session_id,
            properties=properties or {},
            ts=datetime.now(timezone.utc),
        )
        try:
            await self._store.insert_usage_event(event)
        except Exception as exc:  # noqa: BLE001 — telemetry must never block
            _logger.warning(
                "telemetry_record_failed",
                event_type=event_type,
                user_id=user_id,
                error=str(exc),
            )
