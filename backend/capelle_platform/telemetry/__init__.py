"""
Telemetry subsystem — record user-facing events for quota, analytics,
and the admin dashboard.

Public API is re-exported here so callers import from
``capelle_platform.telemetry`` and never reach into concrete
implementations directly.
"""

from capelle_platform.telemetry.base_recorder import BaseTelemetryRecorder
from capelle_platform.telemetry.impl_default import StoreBackedTelemetryRecorder

__all__ = [
    "BaseTelemetryRecorder",
    "StoreBackedTelemetryRecorder",
]
