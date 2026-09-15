"""Data loading and aggregation for the jeugdzorg synthetic CSV corpus.

The loader is responsible for separating IO (CSV reads) from business logic
(forecasting). Aggregations live here because they are pure data shape
transforms; the forecaster receives clean (year, dimension_value, value)
triples and never touches the raw CSV.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from capelle_jeugdzorg_tool.settings import JeugdzorgSettings

_logger = logging.getLogger("capelle_jeugdzorg.data")


class JeugdzorgDataLoader:
    """Loads and aggregates the canonical jeugdzorg CSV.

    The corpus has three CSVs in the workspace root:
      - ``synthetic_jeugdzorg_2020_2025_clean.csv`` (canonical view used here).
      - ``synthetic_jeugdzorg_2020_2025.csv`` (pre-clean).
      - ``synthetic_jeugdzorg_2020_2025.orig.csv`` (origin pre-any-fix).

    Only the clean CSV is loaded by default; the others are available in the
    mounted workspace for the model to diff via shell tools if it wants. The
    loader caches the DataFrame on the instance for the lifetime of one CLI
    invocation so the eda + forecast subcommands don't re-read from disk.
    """

    DIMENSION_COLUMNS: dict[str, str] = {
        "wijk": "Wijk",
        "categorie": "Categorie",
        "aanbieder": "Zorgaanbieder",
        "route": "Route",
        "leeftijd": "Leeftijd",
        "geslacht": "Geslacht",
    }
    METRIC_SUM_COLUMNS: dict[str, str] = {
        "bedrag": "DeclaratieBedrag",
        "aantal": "Declaratie aantal",
    }
    OK_COLUMN: str = "Declaratie aantal OK"
    TOTAL_COLUMN: str = "Declaratie aantal"
    YEAR_COLUMN: str = "Selectiejaar"
    SUPPORTED_METRICS: tuple[str, ...] = ("bedrag", "aantal", "ok_rate")

    def __init__(self, settings: JeugdzorgSettings) -> None:
        self._settings = settings
        self._frame: pd.DataFrame | None = None

    def load(self) -> pd.DataFrame:
        """Load and cache the canonical CSV. Subsequent calls return the cache."""
        if self._frame is None:
            path = self._settings.canonical_csv_path
            if not path.exists():
                raise FileNotFoundError(
                    f"canonical CSV not found at {path}; "
                    f"set JEUGDZORG_DATA or place the file there"
                )
            _logger.info("loading_csv", extra={"path": str(path)})
            self._frame = pd.read_csv(path)
        return self._frame

    def dimension_values(self, dimension: str) -> list[str]:
        """Sorted unique values of ``dimension`` as strings (string-coerced for stable JSON)."""
        self._require_dimension(dimension)
        df = self.load()
        col = self.DIMENSION_COLUMNS[dimension]
        return sorted(str(v) for v in df[col].dropna().unique())

    def aggregate_annual(self, dimension: str, metric: str) -> pd.DataFrame:
        """Aggregate ``metric`` by year × ``dimension_value``.

        For sum-style metrics (``bedrag``, ``aantal``) groups by year and
        dimension and sums the underlying column. For ``ok_rate`` computes
        ``sum(OK) / sum(total)`` per group, with NaN where the denominator is
        zero so downstream consumers can drop or surface the gap explicitly.

        Returns a long-format frame with columns: year_col, dim_col, value.
        """
        self._require_dimension(dimension)
        self._require_metric(metric)
        df = self.load()
        dim_col = self.DIMENSION_COLUMNS[dimension]
        year_col = self.YEAR_COLUMN

        if metric == "ok_rate":
            grouped = (
                df.groupby([year_col, dim_col], dropna=False)
                .agg(ok=(self.OK_COLUMN, "sum"), total=(self.TOTAL_COLUMN, "sum"))
                .reset_index()
            )
            grouped["value"] = grouped["ok"] / grouped["total"].where(
                grouped["total"] > 0
            )
            return grouped[[year_col, dim_col, "value"]]

        metric_col = self.METRIC_SUM_COLUMNS[metric]
        grouped = (
            df.groupby([year_col, dim_col], dropna=False)[metric_col]
            .sum()
            .reset_index()
            .rename(columns={metric_col: "value"})
        )
        return grouped

    def coverage(self) -> dict[str, Any]:
        """Coverage summary the ohrs skill calls first to plan the analysis."""
        df = self.load()
        return {
            "rows": int(len(df)),
            "years": sorted(int(y) for y in df[self.YEAR_COLUMN].dropna().unique()),
            "dimensions": {
                key: {
                    "column": col,
                    "n_values": int(df[col].nunique(dropna=True)),
                    "sample_values": sorted(
                        str(v) for v in df[col].dropna().unique()
                    )[:10],
                }
                for key, col in self.DIMENSION_COLUMNS.items()
            },
            "metrics": list(self.SUPPORTED_METRICS),
            "columns_present": list(df.columns),
        }

    @classmethod
    def _require_dimension(cls, dimension: str) -> None:
        if dimension not in cls.DIMENSION_COLUMNS:
            raise KeyError(
                f"unknown dimension {dimension!r}; "
                f"known: {sorted(cls.DIMENSION_COLUMNS)}"
            )

    @classmethod
    def _require_metric(cls, metric: str) -> None:
        if metric not in cls.SUPPORTED_METRICS:
            raise KeyError(
                f"unknown metric {metric!r}; supported: {list(cls.SUPPORTED_METRICS)}"
            )
