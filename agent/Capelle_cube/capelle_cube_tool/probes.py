"""Twelve typed analytical probes over an OLAP-shaped DataFrame.

Each probe is a class implementing :meth:`run(df, schema) -> ProbeOutput`.
Probes consume only the :class:`~capelle_cube_tool.schema.CubeSchema`
roles, never raw column names — that makes them dataset-agnostic. The
:class:`PROBE_REGISTRY` maps short names (``trajectory``, ``share_drift``,
…) to probe classes so the CLI and the playbook share one source of truth.

A :class:`Finding` is the unit an analyst-LLM reasons about. Every numeric
claim it makes in a report should trace to one. Magnitudes are non-negative
scores used for ranking; the agent picks top-K per probe.

These probes are deliberately shallow and bounded: cube residuals are
magnitude-floored, ratio z-tests require a denominator floor, time-series
analyses degrade gracefully when the dataset has fewer than three years.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from capelle_cube_tool.schema import CubeSchema, materialize_bins

# Thresholds — named so the agent reads and reasons about them rather than
# encountering "magic" floats in branches.
_MIN_OBS_FOR_SLOPE = 3
_SHARE_DRIFT_FLAG_PP_YR = 0.005
_SHARE_DRIFT_HEADLINE_PP_YR = 0.01
_INTERACTION_MAGNITUDE_FRAC = 0.005
_UNIT_PRICE_INTRA_VARIANCE_PCT = 10.0
_UNIT_PRICE_YOY_JUMP_PCT = 20.0
_QUALITY_DEVIATION_PP = 2.0
_QUALITY_MIN_DENOM = 100
_COVERAGE_RESTRICTION_FRAC = 0.6
_PARETO_PERCENTILES: tuple[float, ...] = (0.01, 0.05, 0.10, 0.20)
_HHI_CONCENTRATION_FLAG = 0.25
_ANOMALY_Z_THRESHOLD = 4.0
_SUB_ANNUAL_MONTH_Z = 2.0
_YEAR_VS_POPULATION_PP = 15.0
_TOP_PER_DIM_SHARE = 3
_TOP_INTERACTION_CELLS = 8
_TOP_PRICE_DIVERGENCES = 5
_TOP_COVERAGE_RESTRICTIONS = 5
_RELATIVE_PRICE_FLAG = 0.05

# Default top-N findings the `playbook` compact view returns per probe. The
# agent ranks findings by `magnitude` and drills via `probe <name>` to get
# the full set + evidence dicts when it needs them.
DEFAULT_COMPACT_TOP_N = 8

# Numerator-name hints for "OK/approved" interpretation in quality probe.
_HINTS_OK = ("ok", "approved", "valid", "goedgekeurd", "akkoord")


# ----- typed records returned by every probe ------------------------------ #


@dataclass
class Finding:
    pattern_type: str
    label: str
    description: str
    magnitude: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)
    follow_up_hint: str | None = None

    def compact_dict(self) -> dict[str, Any]:
        """Token-frugal view: drops `evidence` and `follow_up_hint`.

        The `description` field always carries the readable narrative numbers
        the agent needs to write its report; `evidence` only matters when the
        agent wants to render a chart or table for that finding. So the
        compact form keeps `description` and skips `evidence` — the agent
        drills via `capelle-jeugdzorg probe <name>` for the structured data.
        """
        return {
            "pattern_type": self.pattern_type,
            "label": self.label,
            "description": self.description,
            "magnitude": self.magnitude,
        }


@dataclass
class ProbeOutput:
    probe_name: str
    scope: str
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Full output (every finding, every evidence dict)."""
        return {
            "probe_name": self.probe_name,
            "scope": self.scope,
            "findings": [asdict(f) for f in self.findings],
            "notes": self.notes,
        }

    def compact_dict(self, top_n: int = DEFAULT_COMPACT_TOP_N) -> dict[str, Any]:
        """Compact view: top-N findings by `|magnitude|`, no `evidence`.

        Used by `playbook` default mode. Includes both counts so the agent
        knows whether it should drill for more, and the per-probe `scope` so
        the agent knows what the probe measured.
        """
        sorted_f = sorted(
            self.findings, key=lambda f: abs(f.magnitude), reverse=True
        )
        kept = sorted_f[:top_n]
        return {
            "probe_name": self.probe_name,
            "scope": self.scope,
            "n_total": len(self.findings),
            "n_returned": len(kept),
            "findings": [f.compact_dict() for f in kept],
            "notes": self.notes,
        }


# ----- numeric helpers --------------------------------------------------- #


def _slope(years: np.ndarray, vals: np.ndarray) -> float:
    """OLS slope, treating NaNs as missing observations."""
    valid = ~np.isnan(vals)
    if int(valid.sum()) < _MIN_OBS_FOR_SLOPE:
        return 0.0
    return float(np.polyfit(years[valid], vals[valid], 1)[0])


def _yoy_pct(values: pd.Series) -> pd.Series:
    return values.pct_change().fillna(0.0)


# ----- probe ABC --------------------------------------------------------- #


class Probe(ABC):
    """A single analytical operation against an OLAP cube.

    Subclasses are stateless; one instance can run many probes back-to-back.
    All probes follow the same contract: ``run`` returns a fresh
    :class:`ProbeOutput`; raises only on programmer error (schema missing a
    required role); never mutates ``df``.
    """

    name: ClassVar[str]

    @abstractmethod
    def run(self, df: pd.DataFrame, schema: CubeSchema) -> ProbeOutput:
        ...

    # ----- shared helpers ------------------------------------------------ #

    @staticmethod
    def _require(condition: bool, msg: str) -> None:
        if not condition:
            raise ValueError(msg)


# ----- PROBE 1: trajectory ----------------------------------------------- #


class TrajectoryProbe(Probe):
    name: ClassVar[str] = "trajectory"

    def run(self, df: pd.DataFrame, schema: CubeSchema) -> ProbeOutput:
        out = ProbeOutput(
            probe_name=self.name,
            scope=(
                f"Year-level totals + OLS slope for each additive metric and "
                f"ratio pair; flags YoY deltas that diverge from the long-run rate."
            ),
        )
        per_year = df.groupby(schema.time_col)
        agg: dict[str, pd.Series] = {}
        for col in schema.additive_metric_cols:
            agg[col] = per_year[col].sum()
        for num, den in schema.ratio_metric_pairs:
            agg[num] = per_year[num].sum()
            agg[den] = per_year[den].sum()
            agg[f"{num}/{den}"] = agg[num] / agg[den]
        if schema.client_col is not None:
            agg[schema.client_col] = per_year[schema.client_col].nunique()
        per_year_df = pd.DataFrame(agg).sort_index()
        years = per_year_df.index.to_numpy()

        for label, series in per_year_df.items():
            vals = series.to_numpy()
            if len(vals) == 0:
                continue
            first, last = float(vals[0]), float(vals[-1])
            slope = _slope(years, vals)
            growth = (last / first) if first not in (0, 0.0) else float("nan")
            out.findings.append(
                Finding(
                    pattern_type="trend",
                    label=f"{label}: {first:,.2f} → {last:,.2f}"
                    + (f" (×{growth:.2f})" if not np.isnan(growth) else ""),
                    description=(
                        f"Over {int(years[0])}–{int(years[-1])} the metric "
                        f"`{label}` went from {first:,.2f} to {last:,.2f} "
                        + (
                            f"(growth factor ×{growth:.2f}, "
                            + f"{(growth-1)*100:+.1f}%). "
                            if not np.isnan(growth)
                            else ""
                        )
                        + f"OLS slope: {slope:+,.4f}/year."
                    ),
                    magnitude=abs((growth - 1) if not np.isnan(growth) else slope),
                    evidence={
                        "series": {int(y): float(v) for y, v in zip(years, vals)},
                        "first": first,
                        "last": last,
                        "slope_per_year": slope,
                        "growth_factor": None if np.isnan(growth) else growth,
                    },
                )
            )

        # cost-per-client derived trend
        if schema.client_col is not None:
            for amt_col in schema.additive_metric_cols:
                amt = per_year_df[amt_col]
                clients = per_year_df[schema.client_col]
                ratio = amt / clients
                if ratio.iloc[0] == 0:
                    continue
                growth = float(ratio.iloc[-1] / ratio.iloc[0])
                out.findings.append(
                    Finding(
                        pattern_type="trend",
                        label=f"{amt_col} per {schema.client_col}: ×{growth:.2f}",
                        description=(
                            f"Per-{schema.client_col} {amt_col} went "
                            f"{ratio.iloc[0]:,.2f} → {ratio.iloc[-1]:,.2f} "
                            f"(×{growth:.2f}). Indicates {amt_col} growing faster than "
                            f"client count."
                        ),
                        magnitude=abs(growth - 1),
                        evidence={
                            "first": float(ratio.iloc[0]),
                            "last": float(ratio.iloc[-1]),
                            "growth_factor": growth,
                        },
                    )
                )

        # YoY step-change flags on the first additive metric
        if schema.additive_metric_cols:
            primary = schema.additive_metric_cols[0]
            yoy = _yoy_pct(per_year_df[primary])
            longrun = float(yoy.iloc[1:].mean()) if len(yoy) > 1 else 0.0
            for i in range(1, len(per_year_df)):
                if abs(yoy.iloc[i] - longrun) > 0.10:
                    year = int(per_year_df.index[i])
                    out.findings.append(
                        Finding(
                            pattern_type="step_change",
                            label=f"Year {year}: {primary} YoY {yoy.iloc[i]*100:+.1f}%",
                            description=(
                                f"In {year} {primary} changed {yoy.iloc[i]*100:+.1f}% "
                                f"vs. the long-run average of {longrun*100:+.1f}%/yr — "
                                f"out-of-pattern year."
                            ),
                            magnitude=abs(yoy.iloc[i] - longrun),
                            evidence={
                                "year": year,
                                "yoy": float(yoy.iloc[i]),
                                "longrun": longrun,
                            },
                            follow_up_hint="sub_annual",
                        )
                    )
        return out


# ----- PROBE 2: share drift ---------------------------------------------- #


class ShareDriftProbe(Probe):
    name: ClassVar[str] = "share_drift"

    def run(self, df: pd.DataFrame, schema: CubeSchema) -> ProbeOutput:
        df = materialize_bins(df, schema)
        out = ProbeOutput(
            probe_name=self.name,
            scope=(
                f"Per-dim-value share of the year total per additive metric. "
                f"Flags values whose share drift exceeds "
                f"±{_SHARE_DRIFT_FLAG_PP_YR*100:.1f} pp/yr; headlines those "
                f"exceeding ±{_SHARE_DRIFT_HEADLINE_PP_YR*100:.1f}."
            ),
        )
        for dim in schema.dim_cols:
            if dim not in df.columns:
                continue
            for metric in schema.additive_metric_cols:
                ranked = self._drift_for_dim(df, dim, metric, schema.time_col)
                for _, r in ranked.head(_TOP_PER_DIM_SHARE).iterrows():
                    slope = float(r["share_slope_pp_per_yr"]) / 100.0
                    if abs(slope) < _SHARE_DRIFT_FLAG_PP_YR:
                        continue
                    is_headline = abs(slope) >= _SHARE_DRIFT_HEADLINE_PP_YR
                    direction = "rising" if slope > 0 else "falling"
                    out.findings.append(
                        Finding(
                            pattern_type=(
                                "share_shift_headline" if is_headline else "share_shift"
                            ),
                            label=(
                                f"{dim}={r['value']} ({metric}): "
                                f"{r['share_first']*100:.1f}% → "
                                f"{r['share_last']*100:.1f}% "
                                f"({slope*100:+.2f} pp/yr)"
                            ),
                            description=(
                                f"{dim}={r['value']} {direction} on {metric}: "
                                f"share went {r['share_first']*100:.1f}% → "
                                f"{r['share_last']*100:.1f}% "
                                f"(slope {slope*100:+.2f} pp/yr; cum {metric} "
                                f"{r['cum_amount']:,.2f}; growth factor "
                                f"×{r['growth_factor']:.2f})."
                            ),
                            magnitude=abs(slope),
                            evidence={
                                "dim": dim,
                                "value": str(r["value"]),
                                "metric": metric,
                                "share_first": float(r["share_first"]),
                                "share_last": float(r["share_last"]),
                                "slope_pp_per_yr": float(slope * 100),
                                "growth_factor": float(r["growth_factor"]),
                            },
                            follow_up_hint="interactions",
                        )
                    )
        return out

    @staticmethod
    def _drift_for_dim(
        df: pd.DataFrame, dim: str, metric: str, time_col: str
    ) -> pd.DataFrame:
        per_cell = (
            df.groupby([dim, time_col], observed=True)[metric]
            .sum()
            .rename("amount")
            .reset_index()
        )
        per_year = df.groupby(time_col)[metric].sum().rename("year_total")
        per_cell = per_cell.merge(per_year, on=time_col)
        per_cell["share"] = per_cell["amount"] / per_cell["year_total"]
        rows: list[dict[str, Any]] = []
        for val, sub in per_cell.groupby(dim, observed=True):
            sub = sub.sort_values(time_col)
            years = sub[time_col].to_numpy()
            share = sub["share"].to_numpy()
            amounts = sub["amount"].to_numpy()
            rows.append(
                {
                    "value": str(val),
                    "share_first": float(share[0]) if len(share) else float("nan"),
                    "share_last": float(share[-1]) if len(share) else float("nan"),
                    "share_slope_pp_per_yr": _slope(years, share) * 100.0,
                    "cum_amount": float(amounts.sum()),
                    "growth_factor": (
                        float(amounts[-1] / amounts[0])
                        if amounts[0] > 0
                        else float("nan")
                    ),
                }
            )
        return pd.DataFrame(rows).sort_values(
            "share_slope_pp_per_yr", key=lambda s: s.abs(), ascending=False
        )


# ----- shared joint-cube helpers ----------------------------------------- #


def _joint_cube(
    df: pd.DataFrame, dims: list[str], metric: str, time_col: str
) -> pd.DataFrame:
    total_per_year = df.groupby(time_col)[metric].sum()
    overall = float(df[metric].sum())
    if overall == 0:
        return pd.DataFrame()
    shares = {
        d: df.groupby(d, observed=True)[metric].sum() / overall for d in dims
    }
    actual = (
        df.groupby(dims + [time_col], observed=True)[metric]
        .sum()
        .rename("actual")
        .reset_index()
    )
    factor = np.ones(len(actual))
    for d in dims:
        factor = factor * actual[d].map(shares[d]).to_numpy()
    actual["anticipated"] = actual[time_col].map(total_per_year).to_numpy() * factor
    actual["residual"] = actual["actual"] - actual["anticipated"]
    return actual


def _score_joint(
    cube: pd.DataFrame, dims: list[str], time_col: str
) -> pd.DataFrame:
    if cube.empty:
        return cube
    rows: list[dict[str, Any]] = []
    for key, sub in cube.groupby(dims, observed=True):
        sub = sub.sort_values(time_col)
        actual = float(sub["actual"].sum())
        ant = float(sub["anticipated"].sum())
        res = actual - ant
        slope = _slope(sub[time_col].to_numpy(), sub["residual"].to_numpy())
        row: dict[str, Any] = {
            "cum_actual": actual,
            "cum_anticipated": ant,
            "cum_residual": res,
            "residual_slope": slope,
            "pct_deviation": (100.0 * res / ant) if ant else float("nan"),
        }
        if isinstance(key, tuple):
            for d, v in zip(dims, key):
                row[d] = str(v)
        else:
            row[dims[0]] = str(key)
        rows.append(row)
    return pd.DataFrame(rows).sort_values(
        "cum_residual", key=lambda s: s.abs(), ascending=False
    )


# ----- PROBE 3: cross-tab interactions ----------------------------------- #


class InteractionsProbe(Probe):
    name: ClassVar[str] = "interactions"

    def run(self, df: pd.DataFrame, schema: CubeSchema) -> ProbeOutput:
        df = materialize_bins(df, schema)
        if not schema.additive_metric_cols:
            return ProbeOutput(probe_name=self.name, scope="(no additive metric)")
        primary = schema.additive_metric_cols[0]
        scope = (
            f"Sarawagi-style interaction residuals on selected 2- and 3-dim "
            f"joint cubes using {primary}; magnitude floor "
            f"{_INTERACTION_MAGNITUDE_FRAC*100:.1f}% of grand total."
        )
        out = ProbeOutput(probe_name=self.name, scope=scope)
        grand = float(df[primary].sum())
        floor = _INTERACTION_MAGNITUDE_FRAC * grand
        dim_list = list(schema.dim_cols)
        pairs = self._build_pair_list(dim_list)
        for dims in pairs:
            cube = _joint_cube(df, dims, primary, schema.time_col)
            scored = _score_joint(cube, dims, schema.time_col)
            if scored.empty:
                continue
            actionable = scored[scored["cum_residual"].abs() >= floor].head(
                _TOP_INTERACTION_CELLS
            )
            for _, r in actionable.iterrows():
                cell_label = " × ".join(f"{d}={r[d]}" for d in dims)
                sign = "above" if r["cum_residual"] > 0 else "below"
                out.findings.append(
                    Finding(
                        pattern_type="interaction",
                        label=(
                            f"{cell_label}: "
                            f"{r['cum_residual']:+,.2f} {sign} marginal expectation"
                        ),
                        description=(
                            f"On the {' × '.join(dims)} joint cube, {cell_label} "
                            f"delivered {r['cum_actual']:,.2f} vs. marginal-"
                            f"product baseline {r['cum_anticipated']:,.2f} "
                            f"(residual {r['cum_residual']:+,.2f}, "
                            f"{r['pct_deviation']:+.1f}%; residual slope "
                            f"{r['residual_slope']:+,.2f}/yr)."
                        ),
                        magnitude=abs(float(r["cum_residual"])),
                        evidence={
                            "dims": dims,
                            "cell": {d: r[d] for d in dims},
                            "cum_actual": float(r["cum_actual"]),
                            "cum_anticipated": float(r["cum_anticipated"]),
                            "cum_residual": float(r["cum_residual"]),
                            "pct_deviation": float(r["pct_deviation"]),
                            "residual_slope": float(r["residual_slope"]),
                        },
                        follow_up_hint="unit_price",
                    )
                )
        return out

    @staticmethod
    def _build_pair_list(dim_list: list[str]) -> list[list[str]]:
        """Select 3-4 informative dim subsets: every 2-way pair from the
        first three dims plus a 3-way over all three."""
        if len(dim_list) < 2:
            return []
        head = dim_list[:3]
        pairs: list[list[str]] = []
        for i in range(len(head)):
            for j in range(i + 1, len(head)):
                pairs.append([head[i], head[j]])
        if len(head) >= 3:
            pairs.append(head[:3])
        return pairs


# ----- PROBE 4: unit price ----------------------------------------------- #


class UnitPriceProbe(Probe):
    name: ClassVar[str] = "unit_price"

    def run(self, df: pd.DataFrame, schema: CubeSchema) -> ProbeOutput:
        out = ProbeOutput(
            probe_name=self.name,
            scope=(
                "Avg amount per declaratie grouped by price-unit. Surfaces "
                f"intra-dim geographic variance ≥ {_UNIT_PRICE_INTRA_VARIANCE_PCT:.0f}% "
                f"and YoY step jumps ≥ {_UNIT_PRICE_YOY_JUMP_PCT:.0f}%."
            ),
        )
        if len(schema.additive_metric_cols) < 2:
            return out
        amt, qty = schema.additive_metric_cols[0], schema.additive_metric_cols[1]
        work = df[df[qty] > 0].copy()
        work["unit_price"] = work[amt] / work[qty]
        # Geographic spread: WITHIN provider, ACROSS geographic dim. Pick
        # both axes by name hint so the orientation survives any dim_cols
        # ordering — index-based picking flips this on certain datasets.
        if len(schema.dim_cols) >= 2:
            provider_dim = self._pick_provider_dim(schema)
            geo_dim = self._pick_geo_dim(schema, provider_dim)
            self._geographic_spread(
                out, work, provider_dim, geo_dim, schema.price_unit_col
            )
        # YoY jumps per (provider, categorie)
        if len(schema.dim_cols) >= 2:
            self._yoy_jumps(out, work, schema)
        return out

    @staticmethod
    def _pick_provider_dim(schema: CubeSchema) -> str:
        for d in schema.dim_cols:
            if any(h in d.lower() for h in ("aanbieder", "provider", "leverancier")):
                return d
        return schema.dim_cols[1] if len(schema.dim_cols) >= 2 else schema.dim_cols[0]

    @staticmethod
    def _pick_geo_dim(schema: CubeSchema, exclude: str) -> str:
        for d in schema.dim_cols:
            if d == exclude:
                continue
            if any(h in d.lower() for h in ("wijk", "neighborhood", "region", "place", "geo")):
                return d
        for d in schema.dim_cols:
            if d != exclude:
                return d
        return schema.dim_cols[0]

    @staticmethod
    def _pick_categorie_dim(schema: CubeSchema, exclude: str) -> str:
        for d in schema.dim_cols:
            if d == exclude:
                continue
            if any(h in d.lower() for h in ("categorie", "category", "service", "product")):
                return d
        for d in schema.dim_cols:
            if d != exclude:
                return d
        return schema.dim_cols[0]

    @staticmethod
    def _geographic_spread(
        out: ProbeOutput,
        work: pd.DataFrame,
        provider_dim: str,
        geo_dim: str,
        price_unit_col: str | None,
    ) -> None:
        group_cols = (
            [price_unit_col, provider_dim, geo_dim] if price_unit_col else [provider_dim, geo_dim]
        )
        means = work.groupby(group_cols, observed=True)["unit_price"].mean().reset_index()
        anchor = [price_unit_col, provider_dim] if price_unit_col else [provider_dim]
        for keys, ag in means.groupby(anchor):
            if len(ag) < 2:
                continue
            prices = ag["unit_price"].to_numpy()
            mn, mx = float(prices.min()), float(prices.max())
            if mn <= 0:
                continue
            variance_pct = 100.0 * (mx / mn - 1.0)
            if variance_pct < _UNIT_PRICE_INTRA_VARIANCE_PCT:
                continue
            top = ag.sort_values("unit_price", ascending=False).iloc[0]
            bot = ag.sort_values("unit_price", ascending=True).iloc[0]
            keys_t = keys if isinstance(keys, tuple) else (keys,)
            time_unit_label = f" ({keys_t[0]})" if price_unit_col else ""
            provider_label = keys_t[-1]
            out.findings.append(
                Finding(
                    pattern_type="unit_price",
                    label=(
                        f"{provider_dim}={provider_label}{time_unit_label}: "
                        f"{geo_dim}={top[geo_dim]} {top['unit_price']:.2f} vs "
                        f"{geo_dim}={bot[geo_dim]} {bot['unit_price']:.2f} "
                        f"(+{variance_pct:.1f}%)"
                    ),
                    description=(
                        f"{provider_dim}={provider_label}{time_unit_label} charges "
                        f"{top['unit_price']:.2f} in {geo_dim}={top[geo_dim]} "
                        f"vs. {bot['unit_price']:.2f} in {geo_dim}={bot[geo_dim]} "
                        f"— a {variance_pct:.1f}% intra-{provider_dim} spread "
                        f"across {geo_dim}. Possible geographic pricing pattern."
                    ),
                    magnitude=variance_pct,
                    evidence={
                        "provider_dim": provider_dim,
                        "provider_value": str(provider_label),
                        "geo_dim": geo_dim,
                        "price_unit": str(keys_t[0]) if price_unit_col else None,
                        "prices": {
                            str(r[geo_dim]): float(r["unit_price"])
                            for _, r in ag.iterrows()
                        },
                        "variance_pct": variance_pct,
                    },
                )
            )

    @classmethod
    def _yoy_jumps(
        cls, out: ProbeOutput, work: pd.DataFrame, schema: CubeSchema
    ) -> None:
        provider_dim = cls._pick_provider_dim(schema)
        categorie_dim = cls._pick_categorie_dim(schema, provider_dim)
        yoy = (
            work.groupby([provider_dim, categorie_dim, schema.time_col], observed=True)[
                "unit_price"
            ]
            .mean()
            .reset_index()
        )
        for (prov, cat), sub in yoy.groupby([provider_dim, categorie_dim], observed=True):
            sub = sub.sort_values(schema.time_col)
            prices = sub["unit_price"].to_numpy()
            years = sub[schema.time_col].to_numpy()
            for i in range(1, len(prices)):
                if prices[i - 1] <= 0:
                    continue
                change_pct = 100.0 * (prices[i] / prices[i - 1] - 1.0)
                if abs(change_pct) < _UNIT_PRICE_YOY_JUMP_PCT:
                    continue
                out.findings.append(
                    Finding(
                        pattern_type="step_change",
                        label=(
                            f"{provider_dim}={prov} × {categorie_dim}={cat} price "
                            f"{int(years[i-1])}→{int(years[i])}: "
                            f"{prices[i-1]:.2f}→{prices[i]:.2f} "
                            f"({change_pct:+.1f}%)"
                        ),
                        description=(
                            f"Avg unit price for {provider_dim}={prov} on "
                            f"{categorie_dim}={cat} jumped {change_pct:+.1f}% "
                            f"between {int(years[i-1])} and {int(years[i])} "
                            f"({prices[i-1]:.2f}→{prices[i]:.2f}). Possible "
                            f"contract change."
                        ),
                        magnitude=abs(change_pct),
                        evidence={
                            "provider_dim": provider_dim,
                            "provider_value": str(prov),
                            "categorie_dim": categorie_dim,
                            "categorie_value": str(cat),
                            "year_before": int(years[i - 1]),
                            "year_after": int(years[i]),
                            "price_before": float(prices[i - 1]),
                            "price_after": float(prices[i]),
                            "change_pct": change_pct,
                        },
                    )
                )


# ----- PROBE 5: quality ranking ------------------------------------------ #


class QualityRankingProbe(Probe):
    name: ClassVar[str] = "quality_ranking"

    def run(self, df: pd.DataFrame, schema: CubeSchema) -> ProbeOutput:
        scope = (
            "Absolute ratio (numerator/denominator) per provider dim, "
            f"ranked; flags entries > {_QUALITY_DEVIATION_PP:.1f} pp below "
            f"the dataset mean. Cells with < {_QUALITY_MIN_DENOM} denominator "
            "are skipped."
        )
        out = ProbeOutput(probe_name=self.name, scope=scope)
        if not schema.ratio_metric_pairs:
            out.notes.append("no ratio pairs in schema; skipping")
            return out
        if len(schema.dim_cols) == 0:
            out.notes.append("no dim cols; skipping")
            return out
        num, den = schema.ratio_metric_pairs[0]
        provider_dim = self._pick_provider_dim(schema)
        grand = float(df[num].sum() / df[den].sum())
        per = (
            df.groupby(provider_dim, observed=True)
            .agg(num=(num, "sum"), den=(den, "sum"))
            .reset_index()
        )
        per["rate"] = per["num"] / per["den"]
        per = per.sort_values("rate", ascending=True)

        out.findings.append(
            Finding(
                pattern_type="quality",
                label=f"Dataset mean ratio: {grand*100:.2f}%",
                description=(
                    f"Baseline: ratio across the dataset is {grand*100:.2f}% "
                    f"({df[num].sum():,.0f} / {df[den].sum():,.0f})."
                ),
                magnitude=0.0,
                evidence={"grand_rate": grand, "numerator": num, "denominator": den},
            )
        )
        ranking_inline = "; ".join(
            f"{r[provider_dim]} {r['rate']*100:.1f}% (n={int(r['den']):,})"
            for _, r in per.iterrows()
        )
        out.findings.append(
            Finding(
                pattern_type="quality",
                label=f"Per-{provider_dim} ratio ranking",
                description=(
                    f"Ranked low → high: {ranking_inline}. Compare against "
                    f"the dataset mean {grand*100:.2f}% and the "
                    f"-{_QUALITY_DEVIATION_PP:.1f} pp threshold."
                ),
                magnitude=0.0,
                evidence={
                    "provider_dim": provider_dim,
                    "ranking": [
                        {
                            "value": str(r[provider_dim]),
                            "rate": float(r["rate"]),
                            "n": int(r["den"]),
                        }
                        for _, r in per.iterrows()
                    ],
                },
            )
        )
        for _, r in per.iterrows():
            deviation_pp = (r["rate"] - grand) * 100.0
            if r["den"] < _QUALITY_MIN_DENOM:
                continue
            if deviation_pp <= -_QUALITY_DEVIATION_PP:
                out.findings.append(
                    Finding(
                        pattern_type="quality",
                        label=(
                            f"{provider_dim}={r[provider_dim]} ratio "
                            f"{r['rate']*100:.1f}% ({deviation_pp:+.1f} pp vs mean)"
                        ),
                        description=(
                            f"{provider_dim}={r[provider_dim]} has ratio "
                            f"{r['rate']*100:.2f}% across {int(r['den']):,} "
                            f"{den} units — {abs(deviation_pp):.1f} pp under "
                            f"the dataset mean. Quality outlier."
                        ),
                        magnitude=abs(deviation_pp),
                        evidence={
                            "provider_dim": provider_dim,
                            "provider_value": str(r[provider_dim]),
                            "rate": float(r["rate"]),
                            "deviation_pp": deviation_pp,
                            "n": int(r["den"]),
                        },
                    )
                )
        return out

    @staticmethod
    def _pick_provider_dim(schema: CubeSchema) -> str:
        """Heuristic: use the first dim that looks provider-ish, else first dim."""
        for d in schema.dim_cols:
            if any(h in d.lower() for h in ("aanbieder", "provider", "leverancier", "supplier")):
                return d
        return schema.dim_cols[0]


# ----- PROBE 6: coverage ------------------------------------------------- #


class CoverageProbe(Probe):
    name: ClassVar[str] = "coverage"

    def run(self, df: pd.DataFrame, schema: CubeSchema) -> ProbeOutput:
        out = ProbeOutput(
            probe_name=self.name,
            scope=(
                f"For each (outer dim, inner dim) pair, count distinct inner "
                f"values per outer value; flag outer values present in "
                f"< {_COVERAGE_RESTRICTION_FRAC*100:.0f}% of inner values."
            ),
        )
        df = materialize_bins(df, schema)
        dims = list(schema.dim_cols)
        # Probe outer × inner for the first three dims pair-wise
        if len(dims) < 2:
            return out
        for outer in dims[:3]:
            for inner in dims[:3]:
                if outer == inner:
                    continue
                if outer not in df.columns or inner not in df.columns:
                    continue
                total_inner = int(df[inner].nunique())
                per = (
                    df.groupby(outer, observed=True)[inner]
                    .nunique()
                    .reset_index(name="n_distinct")
                )
                per["frac"] = per["n_distinct"] / total_inner
                flagged = per[per["frac"] < _COVERAGE_RESTRICTION_FRAC]
                for _, r in flagged.sort_values("frac").head(
                    _TOP_COVERAGE_RESTRICTIONS
                ).iterrows():
                    present = sorted(
                        df[df[outer] == r[outer]][inner].dropna().unique().tolist()
                    )
                    absent = sorted(
                        set(df[inner].dropna().unique().tolist()) - set(present)
                    )
                    out.findings.append(
                        Finding(
                            pattern_type="coverage",
                            label=(
                                f"{outer}={r[outer]} only appears in "
                                f"{int(r['n_distinct'])} of {total_inner} "
                                f"{inner} values"
                            ),
                            description=(
                                f"{outer}={r[outer]} is present in only "
                                f"{int(r['n_distinct'])} of {total_inner} "
                                f"{inner} values ({r['frac']*100:.0f}% coverage). "
                                f"Present in: {present}. Absent from: {absent}."
                            ),
                            magnitude=1.0 - float(r["frac"]),
                            evidence={
                                "outer_dim": outer,
                                "outer_value": str(r[outer]),
                                "inner_dim": inner,
                                "n_present": int(r["n_distinct"]),
                                "n_total": int(total_inner),
                                "coverage_frac": float(r["frac"]),
                                "present": [str(p) for p in present],
                                "absent": [str(a) for a in absent],
                            },
                        )
                    )
        return out


# ----- PROBE 7: concentration -------------------------------------------- #


class ConcentrationProbe(Probe):
    name: ClassVar[str] = "concentration"

    @staticmethod
    def _pick_stack_dim(schema: CubeSchema) -> str:
        for d in schema.dim_cols:
            if any(h in d.lower() for h in ("categorie", "category", "service", "product")):
                return d
        return schema.dim_cols[0]

    def run(self, df: pd.DataFrame, schema: CubeSchema) -> ProbeOutput:
        out = ProbeOutput(
            probe_name=self.name,
            scope=(
                f"Per-client spend distribution (Pareto at top "
                f"{', '.join(f'{p*100:.0f}%' for p in _PARETO_PERCENTILES)}), "
                f"plus the service-stacking distribution and per-anchor HHI."
            ),
        )
        if schema.client_col is None or not schema.additive_metric_cols:
            out.notes.append("no client_col or metric; skipping")
            return out
        primary = schema.additive_metric_cols[0]
        # Stacking dim = the categorie-like dim (service-type), NOT a
        # geographic dim. Pick by name hint; fall back to dim_cols[0] only
        # if no category-shaped dim exists.
        stack_dim = self._pick_stack_dim(schema)
        per_client = (
            df.groupby(schema.client_col)
            .agg(
                amount=(primary, "sum"),
                n_dims=(stack_dim, "nunique"),
            )
            .sort_values("amount", ascending=False)
            .reset_index()
        )
        total = float(per_client["amount"].sum())
        cum = per_client["amount"].cumsum()
        for pct in _PARETO_PERCENTILES:
            cutoff = max(int(len(per_client) * pct), 1)
            top_amt = float(cum.iloc[cutoff - 1])
            share = 100.0 * top_amt / total if total else 0.0
            out.findings.append(
                Finding(
                    pattern_type="concentration",
                    label=(
                        f"Top {pct*100:.0f}% of clients = "
                        f"{share:.1f}% of {primary}"
                    ),
                    description=(
                        f"The top {cutoff:,} clients (top {pct*100:.0f}% of "
                        f"{len(per_client):,}) account for {top_amt:,.2f} of "
                        f"{total:,.2f} total {primary} — {share:.1f}% "
                        f"concentration."
                    ),
                    magnitude=share,
                    evidence={
                        "top_pct": float(pct),
                        "n_clients_top": cutoff,
                        "n_clients_total": int(len(per_client)),
                        "amount_top": top_amt,
                        "share_pct": share,
                    },
                )
            )
        stacking = per_client["n_dims"].value_counts().sort_index().to_dict()
        total_clients = len(per_client)
        out.findings.append(
            Finding(
                pattern_type="concentration",
                label="Service-stacking distribution",
                description=(
                    f"Out of {total_clients:,} clients: "
                    + ", ".join(
                        f"{int(n)}={int(stacking[n]):,} "
                        f"({100*stacking[n]/total_clients:.1f}%)"
                        for n in sorted(stacking)
                    )
                    + ". Reports both the single-service share (1 dim value) "
                    "AND the heavy-stacker tail (3+) as distinct facts."
                ),
                magnitude=0.0,
                evidence={
                    "distribution": {int(k): int(v) for k, v in stacking.items()},
                    "total_clients": total_clients,
                },
            )
        )
        # Per-anchor HHI (using first two dims if available)
        if len(schema.dim_cols) >= 2:
            anchor, sub_dim = schema.dim_cols[0], schema.dim_cols[1]
            for anchor_val, ag in df.groupby(anchor, observed=True):
                shares = ag.groupby(sub_dim, observed=True)[primary].sum()
                tot = float(shares.sum())
                if tot == 0:
                    continue
                shares = shares / tot
                hhi = float(np.sum(shares.to_numpy() ** 2))
                if hhi >= _HHI_CONCENTRATION_FLAG:
                    top = shares.sort_values(ascending=False).head(2)
                    out.findings.append(
                        Finding(
                            pattern_type="concentration",
                            label=f"{anchor}={anchor_val}: {sub_dim} HHI {hhi:.2f}",
                            description=(
                                f"In {anchor}={anchor_val}, the {sub_dim} "
                                f"Herfindahl index is {hhi:.2f} "
                                f"(>{_HHI_CONCENTRATION_FLAG} = concentrated). "
                                "Top: "
                                + ", ".join(
                                    f"{name} {pct*100:.1f}%"
                                    for name, pct in top.items()
                                )
                                + "."
                            ),
                            magnitude=hhi,
                            evidence={
                                "anchor_dim": anchor,
                                "anchor_value": str(anchor_val),
                                "sub_dim": sub_dim,
                                "hhi": hhi,
                                "top": {str(k): float(v) for k, v in top.items()},
                            },
                        )
                    )
        return out


# ----- PROBE 8: anomaly scan --------------------------------------------- #


class AnomalyScanProbe(Probe):
    name: ClassVar[str] = "anomaly_scan"

    def run(self, df: pd.DataFrame, schema: CubeSchema) -> ProbeOutput:
        out = ProbeOutput(
            probe_name=self.name,
            scope=(
                "Negative-value batches + row-level z-outliers + time-clusters. "
                "Always reports row count, time cluster, AND min/max bedrag "
                "range for negative batches."
            ),
        )
        if not schema.additive_metric_cols:
            return out
        primary = schema.additive_metric_cols[0]
        # Negative batch
        neg = df[df[primary] < 0].copy()
        if not neg.empty:
            per_year = neg[schema.time_col].value_counts().sort_index().to_dict()
            month_label: str | None = None
            month_count = 0
            if schema.period_col is not None and schema.period_col in neg.columns:
                parsed = pd.to_datetime(
                    neg[schema.period_col], errors="coerce", format="mixed"
                )
                month_period = parsed.dt.to_period("M")
                if not month_period.empty:
                    counts = month_period.value_counts()
                    month_label = str(counts.idxmax())
                    month_count = int(counts.max())
            out.findings.append(
                Finding(
                    pattern_type="anomaly",
                    label=(
                        f"{len(neg)} negative-{primary} rows "
                        f"(min {neg[primary].min():,.2f}, "
                        f"max {neg[primary].max():,.2f})"
                    ),
                    description=(
                        f"Found {len(neg):,} rows with negative `{primary}`. "
                        f"Per-year: {per_year}. "
                        + (
                            f"Largest single-month cluster: {month_label} "
                            f"with {month_count} rows. "
                            if month_label
                            else ""
                        )
                        + f"Range min {float(neg[primary].min()):,.2f} max "
                        f"{float(neg[primary].max()):,.2f}. Likely a "
                        f"correction batch."
                    ),
                    magnitude=float(len(neg)),
                    evidence={
                        "n_negative_rows": int(len(neg)),
                        "per_year": {int(k): int(v) for k, v in per_year.items()},
                        "top_month_cluster": month_label,
                        "top_month_count": month_count,
                        "min": float(neg[primary].min()),
                        "max": float(neg[primary].max()),
                    },
                    follow_up_hint="sub_annual",
                )
            )
        # Row-level z-outliers
        series = df[primary]
        mu, sigma = float(series.mean()), float(series.std())
        if sigma > 0:
            z = (series - mu) / sigma
            outliers = df[z.abs() >= _ANOMALY_Z_THRESHOLD]
            if not outliers.empty:
                out.findings.append(
                    Finding(
                        pattern_type="anomaly",
                        label=(
                            f"{len(outliers)} row-level {primary} outliers "
                            f"(|z|≥{_ANOMALY_Z_THRESHOLD})"
                        ),
                        description=(
                            f"{len(outliers)} rows have |z|≥{_ANOMALY_Z_THRESHOLD} "
                            f"on `{primary}`. Investigate for billing errors or "
                            f"genuine high-value cases."
                        ),
                        magnitude=float(len(outliers)),
                        evidence={
                            "n_outliers": int(len(outliers)),
                            "z_threshold": _ANOMALY_Z_THRESHOLD,
                        },
                    )
                )
        return out


# ----- PROBE 9: sub-annual ----------------------------------------------- #


class SubAnnualProbe(Probe):
    name: ClassVar[str] = "sub_annual"

    def run(self, df: pd.DataFrame, schema: CubeSchema) -> ProbeOutput:
        out = ProbeOutput(
            probe_name=self.name,
            scope=(
                f"Month-level totals per dim value per year; flags monthly "
                f"z ≥ {_SUB_ANNUAL_MONTH_Z}σ. Also flags yearly category-vs-"
                f"population gaps ≥ {_YEAR_VS_POPULATION_PP:.0f} pp (both "
                "directions: dips and rebounds — useful for catching COVID-"
                "style year-of-data-start anomalies indirectly)."
            ),
        )
        if not schema.additive_metric_cols:
            return out
        amt_col = (
            schema.additive_metric_cols[1]
            if len(schema.additive_metric_cols) >= 2
            else schema.additive_metric_cols[0]
        )
        if schema.period_col is not None and schema.period_col in df.columns:
            work = df.copy()
            work["__period__"] = pd.to_datetime(
                work[schema.period_col], errors="coerce", format="mixed"
            )
            work["__month__"] = work["__period__"].dt.month
            work = work.dropna(subset=["__period__"])
            if len(schema.dim_cols) >= 1 and not work.empty:
                anchor_dim = schema.dim_cols[
                    min(2, len(schema.dim_cols) - 1)
                ]  # prefer a category-like dim
                per_month = (
                    work.groupby([anchor_dim, schema.time_col, "__month__"], observed=True)[
                        amt_col
                    ]
                    .sum()
                    .reset_index()
                )
                for (cat, year), sub in per_month.groupby(
                    [anchor_dim, schema.time_col], observed=True
                ):
                    if len(sub) < 6:
                        continue
                    vals = sub[amt_col].to_numpy()
                    mu, sigma = float(vals.mean()), float(vals.std())
                    if sigma <= 0:
                        continue
                    for _, r in sub.iterrows():
                        z = (r[amt_col] - mu) / sigma
                        if abs(z) < _SUB_ANNUAL_MONTH_Z:
                            continue
                        out.findings.append(
                            Finding(
                                pattern_type="anomaly",
                                label=(
                                    f"{anchor_dim}={cat} {int(year)}-"
                                    f"{int(r['__month__']):02d}: "
                                    f"{int(r[amt_col])} (z={z:+.1f})"
                                ),
                                description=(
                                    f"In {anchor_dim}={cat} {int(year)} month "
                                    f"{int(r['__month__'])}, {amt_col} was "
                                    f"{int(r[amt_col])} ({z:+.1f}σ from the "
                                    f"year's monthly mean of {mu:.0f})."
                                ),
                                magnitude=abs(z),
                                evidence={
                                    "anchor_dim": anchor_dim,
                                    "anchor_value": str(cat),
                                    "year": int(year),
                                    "month": int(r["__month__"]),
                                    "amount": int(r[amt_col]),
                                    "year_mean": mu,
                                    "z": z,
                                },
                            )
                        )
        # Yearly category-vs-population gaps (both directions)
        if len(schema.dim_cols) >= 1:
            anchor_dim = schema.dim_cols[
                min(2, len(schema.dim_cols) - 1)
            ]
            for cat, sub in df.groupby(anchor_dim, observed=True):
                per_year = sub.groupby(schema.time_col)[amt_col].sum().sort_index()
                total_per_year = df.groupby(schema.time_col)[amt_col].sum().sort_index()
                if len(per_year) < 2:
                    continue
                for i in range(1, len(per_year)):
                    yr = per_year.index[i]
                    if per_year.iloc[i - 1] == 0 or total_per_year.iloc[i - 1] == 0:
                        continue
                    cat_yoy = per_year.iloc[i] / per_year.iloc[i - 1] - 1.0
                    pop_yoy = total_per_year.iloc[i] / total_per_year.iloc[i - 1] - 1.0
                    gap = cat_yoy - pop_yoy
                    if abs(gap) * 100 < _YEAR_VS_POPULATION_PP:
                        continue
                    direction = "rebounded above" if gap > 0 else "fell behind"
                    interpretation = (
                        "→ implies the prior year was a dip for this dim value"
                        if gap > 0
                        else "→ dim-specific contraction"
                    )
                    out.findings.append(
                        Finding(
                            pattern_type="anomaly",
                            label=(
                                f"{anchor_dim}={cat} {int(yr)}: YoY "
                                f"{cat_yoy*100:+.1f}% vs population "
                                f"{pop_yoy*100:+.1f}% ({direction} by "
                                f"{abs(gap)*100:.1f} pp)"
                            ),
                            description=(
                                f"In {int(yr)} {anchor_dim}={cat} grew "
                                f"{cat_yoy*100:+.1f}% YoY while the dataset "
                                f"overall grew {pop_yoy*100:+.1f}%. The dim "
                                f"value {direction} by {abs(gap)*100:.1f} pp "
                                f"{interpretation}."
                            ),
                            magnitude=abs(gap) * 100,
                            evidence={
                                "anchor_dim": anchor_dim,
                                "anchor_value": str(cat),
                                "year": int(yr),
                                "category_yoy": float(cat_yoy),
                                "population_yoy": float(pop_yoy),
                                "gap_pp": float(gap * 100),
                                "direction": "rebound" if gap > 0 else "dip",
                            },
                        )
                    )
        return out


# ----- PROBE 10: relative pricing ---------------------------------------- #


class RelativePricingProbe(Probe):
    name: ClassVar[str] = "relative_pricing"

    def run(self, df: pd.DataFrame, schema: CubeSchema) -> ProbeOutput:
        out = ProbeOutput(
            probe_name=self.name,
            scope=(
                "Per (price_unit, anchor_categorie_dim) cell, compute "
                "population median unit-price; then per-provider weighted "
                "multiplier vs that median. Flags providers > "
                f"{_RELATIVE_PRICE_FLAG*100:.0f}% above or below baseline."
            ),
        )
        if len(schema.additive_metric_cols) < 2 or len(schema.dim_cols) < 2:
            return out
        amt, qty = schema.additive_metric_cols[0], schema.additive_metric_cols[1]
        work = df[df[qty] > 0].copy()
        work["unit_price"] = work[amt] / work[qty]
        provider_dim = self._pick_provider_dim(schema)
        cat_dim = self._pick_categorie_dim(schema, provider_dim)
        group_cols = (
            [schema.price_unit_col, cat_dim, provider_dim]
            if schema.price_unit_col
            else [cat_dim, provider_dim]
        )
        per_cell = (
            work.groupby(group_cols, observed=True)
            .agg(avg_price=("unit_price", "mean"), volume=(qty, "sum"))
            .reset_index()
        )
        anchor = (
            [schema.price_unit_col, cat_dim] if schema.price_unit_col else [cat_dim]
        )
        median = (
            per_cell.groupby(anchor, observed=True)["avg_price"]
            .median()
            .rename("median_price")
            .reset_index()
        )
        per_cell = per_cell.merge(median, on=anchor)
        per_cell["multiplier"] = per_cell["avg_price"] / per_cell["median_price"]

        rows: list[dict[str, Any]] = []
        for prov, sub in per_cell.groupby(provider_dim, observed=True):
            weights = sub["volume"]
            if float(weights.sum()) == 0:
                continue
            wm = float(np.average(sub["multiplier"], weights=weights))
            rows.append(
                {
                    "provider": str(prov),
                    "weighted_multiplier": wm,
                    "n_cells": int(len(sub)),
                    "total_volume": float(weights.sum()),
                }
            )
        rows.sort(key=lambda r: r["weighted_multiplier"], reverse=True)
        ranking_inline = "; ".join(
            f"{r['provider']} {r['weighted_multiplier']:.2f}× "
            f"({(r['weighted_multiplier']-1)*100:+.1f}%)"
            for r in rows
        )
        out.findings.append(
            Finding(
                pattern_type="unit_price",
                label=f"Per-{provider_dim} relative-pricing ranking",
                description=(
                    f"Volume-weighted price multiplier vs population median per "
                    f"({schema.price_unit_col or 'unit'}, {cat_dim}). "
                    f"1.00 = baseline; >1 premium; <1 discount. Ranking "
                    f"(high → low): {ranking_inline}."
                ),
                magnitude=0.0,
                evidence={"ranking": rows, "provider_dim": provider_dim},
            )
        )
        for r in rows:
            deviation = r["weighted_multiplier"] - 1.0
            if abs(deviation) < _RELATIVE_PRICE_FLAG:
                continue
            sign = "premium" if deviation > 0 else "discount"
            out.findings.append(
                Finding(
                    pattern_type="unit_price",
                    label=(
                        f"{provider_dim}={r['provider']}: "
                        f"{r['weighted_multiplier']:.2f}× "
                        f"({deviation*100:+.1f}%, {sign})"
                    ),
                    description=(
                        f"Volume-weighted unit-price multiplier for "
                        f"{provider_dim}={r['provider']} is "
                        f"{r['weighted_multiplier']:.2f}× the population "
                        f"median ({deviation*100:+.1f}%) — a {sign} provider."
                    ),
                    magnitude=abs(deviation),
                    evidence=r,
                )
            )
        return out

    @staticmethod
    def _pick_provider_dim(schema: CubeSchema) -> str:
        for d in schema.dim_cols:
            if any(h in d.lower() for h in ("aanbieder", "provider", "leverancier")):
                return d
        return schema.dim_cols[1] if len(schema.dim_cols) >= 2 else schema.dim_cols[0]

    @staticmethod
    def _pick_categorie_dim(schema: CubeSchema, exclude: str) -> str:
        for d in schema.dim_cols:
            if d == exclude:
                continue
            if any(h in d.lower() for h in ("categorie", "category", "service", "product")):
                return d
        # fall back to first non-excluded
        for d in schema.dim_cols:
            if d != exclude:
                return d
        return schema.dim_cols[0]


# ----- PROBE 11: age alignment ------------------------------------------- #


class AgeAlignmentProbe(Probe):
    name: ClassVar[str] = "age_alignment"

    def run(self, df: pd.DataFrame, schema: CubeSchema) -> ProbeOutput:
        out = ProbeOutput(
            probe_name=self.name,
            scope=(
                "Avg age per anchor-dim value + per-(anchor, age_band) volume "
                "share. Gives the age signature of each anchor value. Probe "
                "is a no-op if no binnable age column was inferred."
            ),
        )
        if not schema.bin_cols:
            out.notes.append("no bin_cols in schema; skipping")
            return out
        df = materialize_bins(df, schema)
        age_col, _, _ = schema.bin_cols[0]
        band_col = f"{age_col}_band"
        anchor_dim = self._pick_categorie_dim(schema)
        per_anchor = (
            df.groupby(anchor_dim, observed=True)
            .agg(avg_age=(age_col, "mean"), median_age=(age_col, "median"))
            .reset_index()
            .sort_values("avg_age")
        )
        if schema.client_col is not None:
            n = (
                df.groupby(anchor_dim, observed=True)[schema.client_col]
                .nunique()
                .rename("n_clients")
                .reset_index()
            )
            per_anchor = per_anchor.merge(n, on=anchor_dim)
        out.findings.append(
            Finding(
                pattern_type="composition",
                label=f"Per-{anchor_dim} avg {age_col}",
                description=(
                    f"Avg `{age_col}` per `{anchor_dim}` (sorted ascending): "
                    + "; ".join(
                        f"{r[anchor_dim]}={r['avg_age']:.1f}"
                        for _, r in per_anchor.iterrows()
                    )
                    + "."
                ),
                magnitude=0.0,
                evidence={
                    "anchor_dim": anchor_dim,
                    "age_col": age_col,
                    "per_value": [
                        {
                            "value": str(r[anchor_dim]),
                            "avg_age": float(r["avg_age"]),
                            "median_age": float(r["median_age"]),
                        }
                        for _, r in per_anchor.iterrows()
                    ],
                },
            )
        )
        if band_col in df.columns:
            metric = schema.additive_metric_cols[
                min(1, len(schema.additive_metric_cols) - 1)
            ]
            band_share = (
                df.groupby([anchor_dim, band_col], observed=True)[metric]
                .sum()
                .reset_index()
            )
            anchor_totals = (
                band_share.groupby(anchor_dim, observed=True)[metric]
                .sum()
                .rename("total")
            )
            band_share = band_share.merge(anchor_totals, on=anchor_dim)
            band_share["share"] = band_share[metric] / band_share["total"]
            rows: list[dict[str, Any]] = []
            for cat, sub in band_share.groupby(anchor_dim, observed=True):
                sub = sub.sort_values("share", ascending=False)
                rows.append(
                    {
                        "value": str(cat),
                        "dominant_band": str(sub.iloc[0][band_col]),
                        "dominant_share": float(sub.iloc[0]["share"]),
                    }
                )
            out.findings.append(
                Finding(
                    pattern_type="composition",
                    label=f"Per-{anchor_dim} dominant {band_col}",
                    description=(
                        f"Dominant `{band_col}` per `{anchor_dim}`: "
                        + "; ".join(
                            f"{r['value']}→{r['dominant_band']} "
                            f"({r['dominant_share']*100:.1f}%)"
                            for r in rows
                        )
                        + "."
                    ),
                    magnitude=0.0,
                    evidence={"anchor_dim": anchor_dim, "rows": rows},
                )
            )
        return out

    @staticmethod
    def _pick_categorie_dim(schema: CubeSchema) -> str:
        for d in schema.dim_cols:
            if any(h in d.lower() for h in ("categorie", "category", "service", "product")):
                return d
        return schema.dim_cols[0]


# ----- registry ---------------------------------------------------------- #


PROBE_REGISTRY: dict[str, type[Probe]] = {
    cls.name: cls
    for cls in (
        TrajectoryProbe,
        ShareDriftProbe,
        InteractionsProbe,
        UnitPriceProbe,
        QualityRankingProbe,
        CoverageProbe,
        ConcentrationProbe,
        AnomalyScanProbe,
        SubAnnualProbe,
        RelativePricingProbe,
        AgeAlignmentProbe,
    )
}


def run_probe(name: str, df: pd.DataFrame, schema: CubeSchema) -> ProbeOutput:
    cls = PROBE_REGISTRY.get(name)
    if cls is None:
        raise KeyError(
            f"unknown probe {name!r}; known: {sorted(PROBE_REGISTRY.keys())}"
        )
    return cls().run(df, schema)


def run_playbook(
    df: pd.DataFrame, schema: CubeSchema
) -> dict[str, ProbeOutput]:
    return {name: cls().run(df, schema) for name, cls in PROBE_REGISTRY.items()}
