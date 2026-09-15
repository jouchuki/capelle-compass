"""Schema inference for arbitrary tabular cubes.

The probe playbook (``probes.py``) needs to know which columns play which
role in the cube: which is time, which are dimensions, which are additive
metrics, which pair up as numerator/denominator for a ratio, which is the
client identifier, and which is the unit-of-billing column for unit-price
analyses. Hard-coding those roles ties the tool to one dataset; inferring
them from headers + value distributions makes the same probes usable on any
OLAP-shaped CSV.

The inference is intentionally simple and explicit: a small set of named
heuristics, each returning a candidate role. The output is a frozen
:class:`CubeSchema` the agent can read as JSON, edit by hand, and pass back
via ``--config``. Inference is a *suggestion*; the agent always wins on
disagreement.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

_logger = logging.getLogger("capelle_cube.schema")

# Heuristic thresholds. Named here so the agent can reason about them and the
# operator can tune via env if needed; not magic numbers buried in branches.
_TIME_COL_MAX_CARDINALITY = 50
_TIME_COL_VALUE_LO = 1900
_TIME_COL_VALUE_HI = 2200
_CLIENT_COL_MIN_UNIQUE_FRAC = 0.20
_DIM_COL_MAX_CARDINALITY = 50
_BIN_COL_NUMERIC_MAX_CARDINALITY = 30
_BIN_COL_NUMERIC_MIN_CARDINALITY = 6
_LEEFTIJD_BINS = [-1, 4, 11, 17, 100]
_LEEFTIJD_LABELS = ["0-4", "5-11", "12-17", "18+"]

# Substring hints for role detection (case-insensitive). Hints are NOT
# definitive — they bias the heuristic without overriding value-based checks.
_HINTS_CLIENT_ID = ("pseudo", "client", "patient", "burger", "bsn", "id")
_HINTS_TIME = ("jaar", "year", "selectiejaar")
_HINTS_PERIOD = ("periode", "datum", "date", "beginperiode", "eindperiode")
_HINTS_PRICE_UNIT = ("tijdseenheid", "unit", "eenheid", "frequentie")
_HINTS_OK_NUMERATOR = ("ok", "approved", "valid", "goedgekeurd", "akkoord")
_HINTS_AGE_BINNABLE = ("leeftijd", "age")
_HINTS_DIM_EXCLUDE = (
    "omschrijving",  # free-text descriptions explode cardinality
    "beginperiode",
    "eindperiode",
)


@dataclass(frozen=True, slots=True)
class CubeSchema:
    """Roles assigned to a tabular dataset's columns.

    Every probe consumes this; the dataset's own column names are the only
    domain vocabulary the agent needs. ``time_col`` and at least one of
    ``additive_metric_cols`` are required; the rest unlock specific probes.
    """

    time_col: str
    dim_cols: tuple[str, ...]
    additive_metric_cols: tuple[str, ...]
    period_col: str | None = None
    client_col: str | None = None
    ratio_metric_pairs: tuple[tuple[str, str], ...] = ()
    price_unit_col: str | None = None
    bin_cols: tuple[tuple[str, tuple[float, ...], tuple[str, ...]], ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "time_col": self.time_col,
            "dim_cols": list(self.dim_cols),
            "additive_metric_cols": list(self.additive_metric_cols),
            "period_col": self.period_col,
            "client_col": self.client_col,
            "ratio_metric_pairs": [list(p) for p in self.ratio_metric_pairs],
            "price_unit_col": self.price_unit_col,
            "bin_cols": [
                {"col": col, "bins": list(bins), "labels": list(labels)}
                for col, bins, labels in self.bin_cols
            ],
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CubeSchema":
        return cls(
            time_col=raw["time_col"],
            dim_cols=tuple(raw.get("dim_cols", ())),
            additive_metric_cols=tuple(raw.get("additive_metric_cols", ())),
            period_col=raw.get("period_col"),
            client_col=raw.get("client_col"),
            ratio_metric_pairs=tuple(
                tuple(p) for p in raw.get("ratio_metric_pairs", ())
            ),
            price_unit_col=raw.get("price_unit_col"),
            bin_cols=tuple(
                (
                    b["col"],
                    tuple(b["bins"]),
                    tuple(b["labels"]),
                )
                for b in raw.get("bin_cols", ())
            ),
            notes=tuple(raw.get("notes", ())),
        )


class SchemaInferrer:
    """Single-method class that walks a DataFrame and emits a :class:`CubeSchema`.

    Public entrypoint is :meth:`infer`. Each private ``_pick_*`` method picks
    one role and removes the chosen columns from the candidate pool so that
    later picks don't double-assign. The order matters: time and client are
    picked first (they remove monotone-numeric and high-cardinality columns
    from the dim pool), then ratio pairs, then dims, then metrics.
    """

    def __init__(self) -> None:
        self._notes: list[str] = []

    def infer(self, df: pd.DataFrame) -> CubeSchema:
        self._notes = []
        cols = list(df.columns)
        time_col = self._pick_time(df, cols)
        cols.remove(time_col)
        period_col = self._pick_period(df, cols)
        if period_col is not None:
            cols.remove(period_col)
        client_col = self._pick_client(df, cols)
        if client_col is not None:
            cols.remove(client_col)
        price_unit_col = self._pick_price_unit(df, cols)
        ratio_pairs = self._pick_ratio_pairs(df, cols)
        # Pick bin_cols BEFORE additive_metrics so a binnable numeric (e.g.
        # Leeftijd) is removed from the additive-metrics pool.
        bin_cols = self._pick_bin_cols(df, cols)
        binned_set = {c for c, _, _ in bin_cols}
        # Don't remove ratio columns from cols yet — denominators can also
        # serve as standalone additive metrics (e.g., "Declaratie aantal" is
        # both the denominator for ok_rate and a metric in its own right).
        additive_metrics = self._pick_additive_metrics(
            df, cols, ratio_pairs, binned_set
        )
        ratio_cols = {c for pair in ratio_pairs for c in pair}
        dim_cols = self._pick_dim_cols(
            df, cols, additive_metrics, bin_cols, price_unit_col, ratio_cols
        )
        return CubeSchema(
            time_col=time_col,
            dim_cols=tuple(dim_cols),
            additive_metric_cols=tuple(additive_metrics),
            period_col=period_col,
            client_col=client_col,
            ratio_metric_pairs=tuple(ratio_pairs),
            price_unit_col=price_unit_col,
            bin_cols=tuple(bin_cols),
            notes=tuple(self._notes),
        )

    # ----- per-role pickers --------------------------------------------- #

    def _pick_time(self, df: pd.DataFrame, candidates: list[str]) -> str:
        scored: list[tuple[int, str]] = []
        for col in candidates:
            score = 0
            name = col.lower()
            if any(h in name for h in _HINTS_TIME):
                score += 50
            if pd.api.types.is_numeric_dtype(df[col]):
                nunique = int(df[col].nunique(dropna=True))
                if nunique > 0 and nunique <= _TIME_COL_MAX_CARDINALITY:
                    score += 20
                    vmin = float(df[col].min())
                    vmax = float(df[col].max())
                    if _TIME_COL_VALUE_LO <= vmin and vmax <= _TIME_COL_VALUE_HI:
                        score += 40
            if score > 0:
                scored.append((score, col))
        if not scored:
            raise ValueError(
                "no candidate time column found — every probe needs one; pass "
                "an explicit `--config` JSON with `time_col` set"
            )
        scored.sort(reverse=True)
        chosen = scored[0][1]
        self._notes.append(f"time_col={chosen!r} (score {scored[0][0]})")
        return chosen

    def _pick_period(self, df: pd.DataFrame, candidates: list[str]) -> str | None:
        for col in candidates:
            name = col.lower()
            if not any(h in name for h in _HINTS_PERIOD):
                continue
            sample = df[col].dropna().head(50)
            parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
            ok = int(parsed.notna().sum())
            if ok >= max(1, int(len(sample) * 0.8)):
                self._notes.append(f"period_col={col!r} (parsed {ok}/{len(sample)} samples)")
                return col
        return None

    def _pick_client(self, df: pd.DataFrame, candidates: list[str]) -> str | None:
        scored: list[tuple[int, str]] = []
        n_rows = max(len(df), 1)
        for col in candidates:
            score = 0
            name = col.lower()
            if any(h in name for h in _HINTS_CLIENT_ID):
                score += 30
            nunique_frac = df[col].nunique(dropna=True) / n_rows
            if nunique_frac >= _CLIENT_COL_MIN_UNIQUE_FRAC:
                score += int(50 * nunique_frac)
            if not pd.api.types.is_numeric_dtype(df[col]) or score >= 30:
                # numeric high-cardinality only counts if name hints client
                pass
            if score >= 30:
                scored.append((score, col))
        if not scored:
            return None
        scored.sort(reverse=True)
        chosen = scored[0][1]
        self._notes.append(f"client_col={chosen!r}")
        return chosen

    def _pick_price_unit(
        self, df: pd.DataFrame, candidates: list[str]
    ) -> str | None:
        for col in candidates:
            if any(h in col.lower() for h in _HINTS_PRICE_UNIT):
                if 2 <= int(df[col].nunique(dropna=True)) <= 12:
                    self._notes.append(f"price_unit_col={col!r}")
                    return col
        return None

    def _pick_ratio_pairs(
        self, df: pd.DataFrame, candidates: list[str]
    ) -> list[tuple[str, str]]:
        """Pair numerator/denominator metrics. Heuristic: a column whose name
        is another column's name extended with an OK/approved hint, with the
        numerator never exceeding the denominator on any row.
        """
        pairs: list[tuple[str, str]] = []
        numeric_cols = [c for c in candidates if pd.api.types.is_numeric_dtype(df[c])]
        for num in numeric_cols:
            if not any(h in num.lower() for h in _HINTS_OK_NUMERATOR):
                continue
            # Find a candidate denominator: same prefix, no OK hint
            best: tuple[int, str] | None = None
            for den in numeric_cols:
                if den == num:
                    continue
                if any(h in den.lower() for h in _HINTS_OK_NUMERATOR):
                    continue
                # name overlap = longest common prefix length
                overlap = self._common_prefix_len(num.lower(), den.lower())
                if overlap < 4:
                    continue
                # numerator must never exceed denominator
                comparable = df[[num, den]].dropna()
                if comparable.empty:
                    continue
                if (comparable[num] > comparable[den]).any():
                    continue
                if best is None or overlap > best[0]:
                    best = (overlap, den)
            if best is not None:
                pairs.append((num, best[1]))
                self._notes.append(f"ratio_pair=({num!r}/{best[1]!r})")
        return pairs

    @staticmethod
    def _common_prefix_len(a: str, b: str) -> int:
        i = 0
        while i < len(a) and i < len(b) and a[i] == b[i]:
            i += 1
        return i

    def _pick_additive_metrics(
        self,
        df: pd.DataFrame,
        candidates: list[str],
        ratio_pairs: list[tuple[str, str]],
        binned_set: set[str],
    ) -> list[str]:
        """Numeric columns suitable for summation.

        Excludes OK-style numerators (already accounted for via ratio_pairs),
        binnable numerics (those are dims), and CategorieNr-style encoded
        categoricals. Always KEEPS ratio denominators as additive metrics —
        being a ratio denominator is a strong signal the column is a real
        count metric, not a coincidentally-numeric dim, regardless of its
        cardinality.
        """
        numerator_set = {n for n, _ in ratio_pairs}
        denominator_set = {d for _, d in ratio_pairs}
        out: list[str] = []
        for col in candidates:
            if col in numerator_set or col in binned_set:
                continue
            if not pd.api.types.is_numeric_dtype(df[col]):
                continue
            # Ratio denominators are always metrics, even if low-cardinality.
            if col in denominator_set:
                out.append(col)
                continue
            nunique = int(df[col].nunique(dropna=True))
            if nunique <= _BIN_COL_NUMERIC_MAX_CARDINALITY:
                continue
            out.append(col)
        # Put the largest-magnitude metric first — that becomes the "primary"
        # metric used by anomaly_scan and interactions. Amount typically beats
        # count by orders of magnitude.
        out.sort(key=lambda c: float(df[c].abs().sum()), reverse=True)
        return out

    def _pick_bin_cols(
        self, df: pd.DataFrame, candidates: list[str]
    ) -> list[tuple[str, tuple[float, ...], tuple[str, ...]]]:
        """Numeric columns with mid-cardinality integers worth binning into bands.

        Currently auto-bins age-like columns into the canonical 0-4/5-11/12-17/18+
        bands; other binnable columns are left to the agent to bin manually.
        """
        out: list[tuple[str, tuple[float, ...], tuple[str, ...]]] = []
        for col in candidates:
            if not pd.api.types.is_numeric_dtype(df[col]):
                continue
            if not any(h in col.lower() for h in _HINTS_AGE_BINNABLE):
                continue
            nunique = int(df[col].nunique(dropna=True))
            if not (
                _BIN_COL_NUMERIC_MIN_CARDINALITY
                <= nunique
                <= _BIN_COL_NUMERIC_MAX_CARDINALITY
            ):
                continue
            out.append((col, tuple(_LEEFTIJD_BINS), tuple(_LEEFTIJD_LABELS)))
            self._notes.append(f"bin_col={col!r} → {_LEEFTIJD_LABELS}")
        return out

    def _pick_dim_cols(
        self,
        df: pd.DataFrame,
        candidates: list[str],
        additive_metrics: list[str],
        bin_cols: list[tuple[str, tuple[float, ...], tuple[str, ...]]],
        price_unit_col: str | None,
        ratio_cols: set[str],
    ) -> list[str]:
        """Anything categorical with reasonable cardinality that isn't already
        playing another role.

        Returned in cardinality-DESCENDING order so the first dims (used by
        interactions, unit_price, concentration) are the information-rich
        ones rather than tiny binary dims like Geslacht.

        Drops near-duplicate dim pairs (CategorieNr ≡ Categorie, Productcode
        ≡ Categorie) by keeping the human-readable name where two dims have
        identical cardinality and one's name is a numeric encoding of the
        other.
        """
        metric_set = set(additive_metrics)
        binned_set = {c for c, _, _ in bin_cols}
        scored: list[tuple[int, str]] = []
        for col in candidates:
            if col in metric_set or col in binned_set or col == price_unit_col:
                continue
            if col in ratio_cols:
                continue
            if any(h in col.lower() for h in _HINTS_DIM_EXCLUDE):
                continue
            nunique = int(df[col].nunique(dropna=True))
            if not (2 <= nunique <= _DIM_COL_MAX_CARDINALITY):
                continue
            scored.append((nunique, col))
        # De-dup pairs whose cardinalities match exactly and whose names
        # share a prefix (Categorie/CategorieNr, Productcode/Omschrijving).
        by_cardinality: dict[int, list[str]] = {}
        for n, c in scored:
            by_cardinality.setdefault(n, []).append(c)
        keep: set[str] = set()
        for n, cols_at in by_cardinality.items():
            if len(cols_at) == 1:
                keep.add(cols_at[0])
                continue
            # Prefer human-readable: drop names ending in 'nr', 'code', 'id'.
            human = [
                c for c in cols_at
                if not re.search(r"(nr|code|id|num)$", c, re.IGNORECASE)
            ]
            keep.update(human or cols_at)
        out = [c for _, c in sorted(scored, reverse=True) if c in keep]
        # Add derived binned dim names (e.g., "Leeftijd_band")
        for col, _bins, _labels in bin_cols:
            derived = f"{col}_band"
            if derived not in out:
                out.append(derived)
        return out


def materialize_bins(df: pd.DataFrame, schema: CubeSchema) -> pd.DataFrame:
    """Apply binning declared in ``schema.bin_cols``, returning a fresh frame.

    Creates ``<col>_band`` columns from the source numeric columns so the
    probes can group on the bands. Idempotent — re-calling on an already-
    binned frame is a no-op.
    """
    work = df.copy()
    for col, bins, labels in schema.bin_cols:
        band_col = f"{col}_band"
        if band_col in work.columns:
            continue
        if col not in work.columns:
            continue
        work[band_col] = pd.cut(work[col], bins=list(bins), labels=list(labels))
    return work


def load_csv(path: Path) -> pd.DataFrame:
    """Read a CSV with sensible defaults; surface a clear error on missing path."""
    if not path.exists():
        raise FileNotFoundError(f"CSV not found at {path}")
    return pd.read_csv(path)


def schema_to_json(schema: CubeSchema) -> str:
    return json.dumps(schema.to_dict(), indent=2, ensure_ascii=False)


def schema_from_json(text: str) -> CubeSchema:
    return CubeSchema.from_dict(json.loads(text))
