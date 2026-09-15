"""
Structured logging with trace-ID propagation.

Every log line carries a trace_id so requests can be correlated
across handler -> queue -> worker -> executor boundaries.
Uses stdlib logging with JSON formatting — no print() anywhere.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")


class TraceContext:
    """
    Context manager that sets a trace_id for the duration of a scope.

    The trace_id is stored in a ContextVar so it propagates correctly
    through async tasks without leaking between concurrent requests.
    """

    @staticmethod
    def new() -> str:
        """Generate and set a fresh trace_id, returning it."""
        tid = uuid.uuid4().hex[:16]
        _trace_id_var.set(tid)
        return tid

    @staticmethod
    def set(trace_id: str) -> None:
        """Propagate an existing trace_id into the current context."""
        _trace_id_var.set(trace_id)

    @staticmethod
    def get() -> str:
        """Retrieve the current trace_id (empty string if unset)."""
        return _trace_id_var.get()


class _JsonFormatter(logging.Formatter):
    """
    Emit each log record as a single JSON line.

    Includes timestamp, level, logger name, message, trace_id, and
    any extra structured fields passed via the `extra` dict.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Serialise a LogRecord into a JSON string with trace context."""
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": _trace_id_var.get(),
        }
        if hasattr(record, "structured"):
            entry.update(record.structured)  # type: ignore[arg-type]
        if record.exc_info and record.exc_info[1] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


class StructuredLogger:
    """
    Thin wrapper around stdlib logging that injects trace_id automatically.

    Every major component receives a StructuredLogger at construction time
    via the Builder.  This keeps logging consistent and testable.
    """

    def __init__(self, name: str, level: str = "INFO") -> None:
        """
        Initialise a named structured logger.

        Args:
            name: Logger name, typically the module or class name.
            level: Minimum log level as a string.
        """
        self._logger = logging.getLogger(name)
        self._logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(_JsonFormatter())
            self._logger.addHandler(handler)
            self._logger.propagate = False

    def info(self, msg: str, **kwargs: Any) -> None:
        """Log at INFO level with optional structured fields."""
        self._logger.info(msg, extra={"structured": kwargs})

    def warning(self, msg: str, **kwargs: Any) -> None:
        """Log at WARNING level with optional structured fields."""
        self._logger.warning(msg, extra={"structured": kwargs})

    def error(self, msg: str, **kwargs: Any) -> None:
        """Log at ERROR level with optional structured fields."""
        self._logger.error(msg, extra={"structured": kwargs})

    def debug(self, msg: str, **kwargs: Any) -> None:
        """Log at DEBUG level with optional structured fields."""
        self._logger.debug(msg, extra={"structured": kwargs})

    def exception(self, msg: str, **kwargs: Any) -> None:
        """Log at ERROR level with traceback and optional structured fields."""
        self._logger.exception(msg, extra={"structured": kwargs})


def get_logger(name: str, level: str = "INFO") -> StructuredLogger:
    """
    Factory function for obtaining a StructuredLogger.

    Standalone function lives in observability (utility module).
    """
    return StructuredLogger(name, level)
