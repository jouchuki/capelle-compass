"""Public API for the job executor layer."""

from capelle_platform.executor.base_executor import BaseExecutor
from capelle_platform.executor.impl_ohrs import OhrsExecutor

__all__: list[str] = ["BaseExecutor", "OhrsExecutor"]
