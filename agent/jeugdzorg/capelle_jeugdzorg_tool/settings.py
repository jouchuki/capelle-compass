"""Centralized configuration for the jeugdzorg analysis tool.

Settings are env-var driven so deployment (host paths, defaults) can be
overridden without code changes — no magic numbers or hardcoded paths
elsewhere in the package.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1]
_DEFAULT_CANONICAL_CSV = "synthetic_jeugdzorg_2020_2025_clean.csv"
_DEFAULT_HORIZON_YEARS = 3
_DEFAULT_CONFIDENCE = 0.95
_DEFAULT_MIN_OBSERVATIONS = 3


def _data_dir_from_env() -> Path:
    return Path(os.environ.get("JEUGDZORG_DATA", str(_DEFAULT_DATA_DIR)))


@dataclass(frozen=True, slots=True)
class JeugdzorgSettings:
    """Immutable runtime configuration consumed by every component in the tool.

    Why immutable: the CLI is short-lived per invocation, but the same settings
    instance is shared between the data loader and the forecaster within a run.
    Freezing prevents accidental mutation across components.
    """

    data_dir: Path = field(default_factory=_data_dir_from_env)
    canonical_csv: str = _DEFAULT_CANONICAL_CSV
    default_horizon_years: int = _DEFAULT_HORIZON_YEARS
    default_confidence: float = _DEFAULT_CONFIDENCE
    min_observations: int = _DEFAULT_MIN_OBSERVATIONS

    @property
    def canonical_csv_path(self) -> Path:
        """Absolute path of the canonical clean CSV used for all analyses."""
        return self.data_dir / self.canonical_csv

    @classmethod
    def from_env(cls) -> "JeugdzorgSettings":
        """Build a fresh settings instance from the current process environment.

        Returns a frozen instance ready to inject into the data loader and the
        forecaster. Callers should treat the returned value as a singleton for
        the duration of one CLI invocation.
        """
        return cls()


_logger = logging.getLogger("capelle_jeugdzorg")
