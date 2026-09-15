"""Re-export of the ``Mode`` domain type for executor-side consumers.

The canonical definition lives in ``capelle_platform.models.mode``; this
module re-exports it so callers under ``capelle_platform.executor.modes``
can import without crossing the package boundary explicitly. Keeping the
canonical type in ``models`` preserves the leaf-module dependency rule.
"""

from __future__ import annotations

from capelle_platform.models.mode import DEFAULT_MODE, SUPPORTED_MODES, Mode

__all__ = ["DEFAULT_MODE", "Mode", "SUPPORTED_MODES"]
