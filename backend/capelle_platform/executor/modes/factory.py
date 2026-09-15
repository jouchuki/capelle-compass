"""Factory that maps a ``Mode`` literal to its concrete ``ModeConfig``.

Keeps the executor decoupled from the concrete implementations: it asks the
factory for a config, the factory looks the mode up in a registry, and the
right subclass is returned. Adding a new mode requires (a) extending the
``Mode`` literal in ``mode_type.py``, (b) writing a new ``impl_*.py``, and
(c) registering it in the ``_REGISTRY`` dict below — no executor change.
"""

from __future__ import annotations

from typing import Callable, ClassVar

from capelle_platform.executor.modes.base_mode import ModeConfig
from capelle_platform.executor.modes.impl_groeikern import GroeikernModeConfig
from capelle_platform.executor.modes.impl_jeugdzorg import JeugdzorgModeConfig
from capelle_platform.executor.modes.mode_type import Mode, SUPPORTED_MODES
from capelle_platform.settings import Settings


class ModeConfigFactory:
    """Stateless lookup from ``Mode`` literal to ``ModeConfig`` instance."""

    _REGISTRY: ClassVar[dict[Mode, Callable[[Settings], ModeConfig]]] = {
        "groeikern": GroeikernModeConfig,
        "jeugdzorg": JeugdzorgModeConfig,
    }

    @classmethod
    def from_mode(cls, mode: Mode, settings: Settings) -> ModeConfig:
        """Return a fresh ``ModeConfig`` for ``mode``.

        Raises ``ValueError`` if ``mode`` is not a member of ``Mode`` — should
        never happen at runtime because the API validates the field, but the
        guard catches regressions in the chat handler.
        """
        builder = cls._REGISTRY.get(mode)
        if builder is None:
            raise ValueError(
                f"unknown mode {mode!r}; supported: {list(SUPPORTED_MODES)}"
            )
        return builder(settings)
