"""Per-mode configuration package for the ohrs executor.

The executor stays mode-agnostic; this package supplies a ``ModeConfig`` for
each analysis mode (``groeikern`` and ``jeugdzorg``) and a factory that picks
the right one off the incoming ``JobMessage.mode``. New modes drop a new
``impl_*.py`` here and register it in the factory — no executor change needed.
"""

from __future__ import annotations

from capelle_platform.executor.modes.base_mode import ModeConfig
from capelle_platform.executor.modes.factory import ModeConfigFactory
from capelle_platform.executor.modes.impl_groeikern import GroeikernModeConfig
from capelle_platform.executor.modes.impl_jeugdzorg import JeugdzorgModeConfig
from capelle_platform.executor.modes.mode_type import Mode, SUPPORTED_MODES

__all__ = [
    "GroeikernModeConfig",
    "JeugdzorgModeConfig",
    "Mode",
    "ModeConfig",
    "ModeConfigFactory",
    "SUPPORTED_MODES",
]
