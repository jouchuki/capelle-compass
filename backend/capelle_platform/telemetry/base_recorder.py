"""
Abstract contract for the telemetry recorder.

A ``BaseTelemetryRecorder`` accepts a typed event, enriches it with
server-side fields (id, timestamp) that the client must not supply,
and dispatches it to a sink. Implementations choose the sink — the
MVP writes to the same SQLite store as the chat history; a future
implementation might batch to Kafka or a BigQuery tap.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from capelle_platform.models.telemetry import TelemetryEventType


class BaseTelemetryRecorder(ABC):
    """
    Contract for recording usage events.

    The recorder owns *policy* — what fields are always set, how
    failures are handled, dedup rules — while the underlying store
    owns *mechanics*. Handlers call ``record`` and never care how the
    event lands.
    """

    @abstractmethod
    async def record(
        self,
        *,
        user_id: str,
        event_type: TelemetryEventType,
        session_id: str | None = None,
        properties: dict[str, object] | None = None,
    ) -> None:
        """
        Record one event for ``user_id``.

        Implementations MUST NOT raise on sink failure: the caller is
        on a hot request path and cannot afford analytics outages to
        break the user experience. Errors are logged and swallowed.
        """
