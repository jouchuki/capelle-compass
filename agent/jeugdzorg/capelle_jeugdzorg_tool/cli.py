"""Command-line entry point for the jeugdzorg analysis tool.

JSON stdout on success, JSON-on-stderr on error, mirroring the groeikernen
CLI convention so the ohrs skill orchestrates both tools the same way.

Subcommands:
    coverage       row counts, years, per-dimension cardinalities + sample values
    eda            annual aggregates by dimension x metric, with a chart hint
    forecast       OLS linear regression + prediction interval per dimension slice
    infer-schema   auto-infer time/dim/metric/client roles for any CSV
    probe          run one named probe from the 11-probe registry
    playbook       run all 11 probes and emit a single combined JSON
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from capelle_jeugdzorg_tool.base_forecaster import BaseForecaster, ForecastResult
from capelle_jeugdzorg_tool.data import JeugdzorgDataLoader
from capelle_jeugdzorg_tool.impl_ols import ForecastError, OLSLinearForecaster
from capelle_jeugdzorg_tool.probes import (
    DEFAULT_COMPACT_TOP_N,
    PROBE_REGISTRY,
    run_playbook,
    run_probe,
)
from capelle_jeugdzorg_tool.schema import (
    CubeSchema,
    SchemaInferrer,
    load_csv,
    schema_from_json,
    schema_to_json,
)
from capelle_jeugdzorg_tool.settings import JeugdzorgSettings

signal.signal(signal.SIGPIPE, signal.SIG_DFL)

_EXIT_OK = 0
_EXIT_ERROR = 1
_EXIT_USAGE = 2
_EXIT_FORECAST = 3

_logger = logging.getLogger("capelle_jeugdzorg.cli")


class JeugdzorgCLI:
    """Wires the data loader + forecaster behind argparse subcommands.

    The class is constructed once per CLI invocation by ``main``; it depends
    only on the ``BaseForecaster`` abstraction so a different forecaster could
    be injected without changing this surface.
    """

    def __init__(
        self,
        loader: JeugdzorgDataLoader,
        forecaster: BaseForecaster,
        settings: JeugdzorgSettings,
    ) -> None:
        self._loader = loader
        self._forecaster = forecaster
        self._settings = settings

    @classmethod
    def build_parser(cls, settings: JeugdzorgSettings) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="capelle-jeugdzorg",
            description=(
                "EDA + linear-regression future-trends CLI for the jeugdzorg "
                "synthetic corpus. JSON stdout on success."
            ),
        )
        subparsers = parser.add_subparsers(dest="command", required=True)

        subparsers.add_parser(
            "coverage",
            help="Row counts, years, per-dimension cardinalities + sample values.",
        )

        dim_choices = sorted(JeugdzorgDataLoader.DIMENSION_COLUMNS.keys())
        metric_choices = list(JeugdzorgDataLoader.SUPPORTED_METRICS)

        p_eda = subparsers.add_parser(
            "eda",
            help="Annual aggregates by dimension x metric (chart-ready).",
        )
        p_eda.add_argument("--dimension", required=True, choices=dim_choices)
        p_eda.add_argument("--metric", required=True, choices=metric_choices)

        p_fc = subparsers.add_parser(
            "forecast",
            help="OLS linear regression + prediction interval per dimension slice.",
        )
        p_fc.add_argument("--dimension", required=True, choices=dim_choices)
        p_fc.add_argument("--metric", required=True, choices=metric_choices)
        p_fc.add_argument(
            "--value",
            required=False,
            default=None,
            help=(
                "Specific dimension value to forecast. Omit to forecast every "
                "value in the dimension and return one entry per slice."
            ),
        )
        p_fc.add_argument(
            "--years",
            type=int,
            default=settings.default_horizon_years,
            help=(
                f"Periods to project past the last observed year "
                f"(default: {settings.default_horizon_years})."
            ),
        )
        p_fc.add_argument(
            "--confidence",
            type=float,
            default=settings.default_confidence,
            help=(
                f"Two-sided prediction-interval coverage "
                f"(default: {settings.default_confidence})."
            ),
        )

        # --- schema-inference + probe subcommands (dataset-agnostic) ----- #

        p_schema = subparsers.add_parser(
            "infer-schema",
            help=(
                "Print the inferred CubeSchema for a CSV. Pass --csv to point "
                "at any file; defaults to the canonical jeugdzorg CSV."
            ),
        )
        p_schema.add_argument("--csv", default=None, help="CSV path (default: canonical)")

        probe_choices = sorted(PROBE_REGISTRY.keys())
        p_probe = subparsers.add_parser(
            "probe",
            help=(
                "Run one named probe and emit ProbeOutput JSON (full evidence "
                "by default). Probes: " + ", ".join(probe_choices)
            ),
        )
        p_probe.add_argument("name", choices=probe_choices)
        p_probe.add_argument("--csv", default=None, help="CSV path (default: canonical)")
        p_probe.add_argument(
            "--config",
            default=None,
            help=(
                "Optional path to a CubeSchema JSON file (override inference). "
                "Get one via `capelle-jeugdzorg infer-schema > schema.json`."
            ),
        )
        p_probe.add_argument(
            "--compact",
            action="store_true",
            help="Drop evidence dicts and truncate to top-N (default: full).",
        )
        p_probe.add_argument(
            "--top-n",
            type=int,
            default=DEFAULT_COMPACT_TOP_N,
            help=(
                f"With --compact: keep top-N findings by |magnitude| "
                f"(default {DEFAULT_COMPACT_TOP_N})."
            ),
        )

        p_play = subparsers.add_parser(
            "playbook",
            help=(
                "Run all 11 probes and emit one combined JSON. Default: "
                "COMPACT mode — top-N per probe, no evidence dicts. Add "
                "--full for the unabridged dump (every finding, every "
                "evidence)."
            ),
        )
        p_play.add_argument("--csv", default=None, help="CSV path (default: canonical)")
        p_play.add_argument(
            "--config",
            default=None,
            help="Optional CubeSchema JSON file (override inference).",
        )
        p_play.add_argument(
            "--full",
            action="store_true",
            help=(
                "Return every finding with full evidence dict (~10x more "
                "tokens). Default is compact: top-N per probe, no evidence."
            ),
        )
        p_play.add_argument(
            "--top-n",
            type=int,
            default=DEFAULT_COMPACT_TOP_N,
            help=(
                f"Compact mode: keep top-N findings by |magnitude| per probe "
                f"(default {DEFAULT_COMPACT_TOP_N})."
            ),
        )

        return parser

    def run(self, argv: list[str] | None = None) -> int:
        parser = self.build_parser(self._settings)
        args = parser.parse_args(argv)
        try:
            if args.command == "coverage":
                payload = self._cmd_coverage()
            elif args.command == "eda":
                payload = self._cmd_eda(args.dimension, args.metric)
            elif args.command == "forecast":
                payload = self._cmd_forecast(
                    dimension=args.dimension,
                    metric=args.metric,
                    value=args.value,
                    years=args.years,
                    confidence=args.confidence,
                )
            elif args.command == "infer-schema":
                payload = self._cmd_infer_schema(args.csv)
            elif args.command == "probe":
                payload = self._cmd_probe(
                    args.name, args.csv, args.config, args.compact, args.top_n
                )
            elif args.command == "playbook":
                payload = self._cmd_playbook(
                    args.csv, args.config, args.full, args.top_n
                )
            else:
                sys.stderr.write(
                    json.dumps({"error": f"unknown command {args.command!r}"})
                    + "\n"
                )
                return _EXIT_USAGE
        except FileNotFoundError as exc:
            sys.stderr.write(json.dumps({"error": str(exc)}) + "\n")
            return _EXIT_ERROR
        except (KeyError, ValueError) as exc:
            sys.stderr.write(json.dumps({"error": str(exc)}) + "\n")
            return _EXIT_USAGE
        except ForecastError as exc:
            sys.stderr.write(json.dumps({"error": str(exc)}) + "\n")
            return _EXIT_FORECAST
        except Exception as exc:
            _logger.exception("unhandled_cli_error")
            sys.stderr.write(
                json.dumps({"error": f"{type(exc).__name__}: {exc}"}) + "\n"
            )
            return _EXIT_ERROR

        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return _EXIT_OK

    def _cmd_coverage(self) -> dict[str, Any]:
        return self._loader.coverage()

    def _cmd_eda(self, dimension: str, metric: str) -> dict[str, Any]:
        df = self._loader.aggregate_annual(dimension, metric)
        dim_col = JeugdzorgDataLoader.DIMENSION_COLUMNS[dimension]
        year_col = JeugdzorgDataLoader.YEAR_COLUMN
        rows: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            raw_value = row["value"]
            rows.append(
                {
                    "year": int(row[year_col]),
                    "dimension_value": str(row[dim_col]),
                    "value": None if pd.isna(raw_value) else float(raw_value),
                }
            )
        return {
            "dimension": dimension,
            "metric": metric,
            "series": rows,
            "chart": {
                "type": "line",
                "x": "year",
                "y": "value",
                "group_by": "dimension_value",
                "title": f"{metric} per {dimension} per jaar",
            },
        }

    def _cmd_forecast(
        self,
        dimension: str,
        metric: str,
        value: str | None,
        years: int,
        confidence: float,
    ) -> dict[str, Any]:
        values_to_forecast = (
            [value] if value is not None else self._loader.dimension_values(dimension)
        )

        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for dimension_value in values_to_forecast:
            try:
                result = self._forecaster.forecast(
                    dimension=dimension,
                    dimension_value=dimension_value,
                    metric=metric,
                    periods_ahead=years,
                    confidence=confidence,
                )
                results.append(self._serialize(result))
            except ForecastError as exc:
                errors.append({"dimension_value": dimension_value, "error": str(exc)})

        return {
            "dimension": dimension,
            "metric": metric,
            "horizon_years": years,
            "confidence": confidence,
            "results": results,
            "errors": errors,
            "chart": {
                "type": "forecast",
                "x": "period",
                "y": "predicted",
                "lower": "lower",
                "upper": "upper",
                "group_by": "dimension_value",
                "title": f"{metric}-forecast per {dimension} (+{years}j, {int(confidence * 100)}% PI)",
            },
        }

    # ----- schema + probe wiring ---------------------------------------- #

    def _resolve_csv(self, csv_arg: str | None) -> Path:
        """Use the provided CSV path or fall back to the canonical one."""
        if csv_arg:
            return Path(csv_arg).expanduser()
        return self._settings.canonical_csv_path

    def _resolve_schema(
        self, df: pd.DataFrame, config_path: str | None
    ) -> CubeSchema:
        if config_path:
            text = Path(config_path).expanduser().read_text(encoding="utf-8")
            return schema_from_json(text)
        return SchemaInferrer().infer(df)

    def _cmd_infer_schema(self, csv_arg: str | None) -> dict[str, Any]:
        path = self._resolve_csv(csv_arg)
        df = load_csv(path)
        schema = SchemaInferrer().infer(df)
        return {"csv": str(path), "n_rows": int(len(df)), "schema": schema.to_dict()}

    def _cmd_probe(
        self,
        name: str,
        csv_arg: str | None,
        config_path: str | None,
        compact: bool,
        top_n: int,
    ) -> dict[str, Any]:
        path = self._resolve_csv(csv_arg)
        df = load_csv(path)
        schema = self._resolve_schema(df, config_path)
        out = run_probe(name, df, schema)
        rendered = out.compact_dict(top_n) if compact else out.to_dict()
        return {"csv": str(path), "schema": schema.to_dict(), "output": rendered}

    def _cmd_playbook(
        self,
        csv_arg: str | None,
        config_path: str | None,
        full: bool,
        top_n: int,
    ) -> dict[str, Any]:
        path = self._resolve_csv(csv_arg)
        df = load_csv(path)
        schema = self._resolve_schema(df, config_path)
        outputs = run_playbook(df, schema)
        # Compact schema view in the default mode — the full dump keeps notes etc.
        schema_view = schema.to_dict() if full else self._compact_schema(schema)
        probes_view = {
            name: (out.to_dict() if full else out.compact_dict(top_n))
            for name, out in outputs.items()
        }
        payload: dict[str, Any] = {
            "csv": str(path),
            "n_rows": int(len(df)),
            "schema": schema_view,
            "mode": "full" if full else "compact",
            "probes": probes_view,
        }
        if not full:
            payload["hint"] = (
                "Compact view: top-N findings per probe by |magnitude|, "
                "no `evidence` dicts. Call "
                "`capelle-jeugdzorg probe <name>` to get the full output "
                "(all findings + structured evidence) for one probe — "
                "use this when you need values for charts/tables."
            )
        return payload

    @staticmethod
    def _compact_schema(schema: CubeSchema) -> dict[str, Any]:
        """Strip the verbose `notes` + redundant fields for the playbook header.

        The full schema is fetchable via `capelle-jeugdzorg infer-schema`.
        """
        return {
            "time_col": schema.time_col,
            "dim_cols": list(schema.dim_cols),
            "additive_metric_cols": list(schema.additive_metric_cols),
            "ratio_metric_pairs": [list(p) for p in schema.ratio_metric_pairs],
            "client_col": schema.client_col,
            "price_unit_col": schema.price_unit_col,
            "bin_cols": [c for c, _, _ in schema.bin_cols],
        }

    @staticmethod
    def _serialize(result: ForecastResult) -> dict[str, Any]:
        return {
            "dimension_value": result.dimension_value,
            "slope": result.slope,
            "intercept": result.intercept,
            "r_squared": result.r_squared,
            "historical": [asdict(point) for point in result.historical],
            "forecast": [asdict(point) for point in result.forecast],
        }

    @classmethod
    def main(cls, argv: list[str] | None = None) -> int:
        """Entry point for the ``capelle-jeugdzorg`` console script."""
        settings = JeugdzorgSettings.from_env()
        loader = JeugdzorgDataLoader(settings)
        forecaster = OLSLinearForecaster(loader)
        return cls(loader=loader, forecaster=forecaster, settings=settings).run(argv)


if __name__ == "__main__":
    sys.exit(JeugdzorgCLI.main())
