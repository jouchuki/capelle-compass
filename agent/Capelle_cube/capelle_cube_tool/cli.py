"""Command-line entry point for ``capelle-cube``.

A general OLAP-style probe playbook for any tabular CSV. JSON stdout on
success, JSON-on-stderr on error — same convention as the other Capelle
agent CLIs (``capelle-beleid``, ``capelle-budget``, ``capelle-jeugdzorg``).

Subcommands:
    infer-schema    auto-detect time / dim / metric / client / ratio roles
    probe <name>    run one of the 11 probes against the CSV
    playbook        run all 11 probes (compact view by default)
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
from pathlib import Path
from typing import Any

from capelle_cube_tool.probes import (
    DEFAULT_COMPACT_TOP_N,
    PROBE_REGISTRY,
    run_playbook,
    run_probe,
)
from capelle_cube_tool.schema import (
    CubeSchema,
    SchemaInferrer,
    load_csv,
    schema_from_json,
)

signal.signal(signal.SIGPIPE, signal.SIG_DFL)

_EXIT_OK = 0
_EXIT_ERROR = 1
_EXIT_USAGE = 2

_logger = logging.getLogger("capelle_cube.cli")


class CubeCLI:
    """Wires schema inference + probe registry behind argparse subcommands."""

    @classmethod
    def build_parser(cls) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="capelle-cube",
            description=(
                "General OLAP-style analytical playbook. Pass --csv at any "
                "tabular file with a time column + at least one dimension + "
                "at least one numeric metric. JSON stdout on success."
            ),
        )
        subparsers = parser.add_subparsers(dest="command", required=True)

        p_schema = subparsers.add_parser(
            "infer-schema",
            help=(
                "Print the inferred CubeSchema for a CSV — sanity check / "
                "override starting point."
            ),
        )
        p_schema.add_argument("--csv", required=True, help="CSV path")

        probe_choices = sorted(PROBE_REGISTRY.keys())
        p_probe = subparsers.add_parser(
            "probe",
            help=(
                "Run one named probe and emit ProbeOutput JSON (FULL evidence "
                "by default). Probes: " + ", ".join(probe_choices)
            ),
        )
        p_probe.add_argument("name", choices=probe_choices)
        p_probe.add_argument("--csv", required=True)
        p_probe.add_argument(
            "--config",
            default=None,
            help=(
                "Optional path to a CubeSchema JSON file to override the "
                "auto-inferred schema. Get one via "
                "`capelle-cube infer-schema --csv X > schema.json`."
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
                "COMPACT — top-N per probe, no evidence dicts (~25 KB). "
                "Add --full for the unabridged dump (every finding + "
                "evidence, ~4x more tokens)."
            ),
        )
        p_play.add_argument("--csv", required=True)
        p_play.add_argument("--config", default=None)
        p_play.add_argument(
            "--full",
            action="store_true",
            help="Return every finding with full evidence dict.",
        )
        p_play.add_argument(
            "--top-n",
            type=int,
            default=DEFAULT_COMPACT_TOP_N,
        )

        return parser

    def run(self, argv: list[str] | None = None) -> int:
        parser = self.build_parser()
        args = parser.parse_args(argv)
        try:
            if args.command == "infer-schema":
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
        except Exception as exc:
            _logger.exception("unhandled_cli_error")
            sys.stderr.write(
                json.dumps({"error": f"{type(exc).__name__}: {exc}"}) + "\n"
            )
            return _EXIT_ERROR

        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return _EXIT_OK

    # ----- subcommand handlers ------------------------------------------ #

    @staticmethod
    def _resolve_schema(df, config_path: str | None) -> CubeSchema:
        if config_path:
            text = Path(config_path).expanduser().read_text(encoding="utf-8")
            return schema_from_json(text)
        return SchemaInferrer().infer(df)

    def _cmd_infer_schema(self, csv: str) -> dict[str, Any]:
        path = Path(csv).expanduser()
        df = load_csv(path)
        schema = SchemaInferrer().infer(df)
        return {"csv": str(path), "n_rows": int(len(df)), "schema": schema.to_dict()}

    def _cmd_probe(
        self,
        name: str,
        csv: str,
        config_path: str | None,
        compact: bool,
        top_n: int,
    ) -> dict[str, Any]:
        path = Path(csv).expanduser()
        df = load_csv(path)
        schema = self._resolve_schema(df, config_path)
        out = run_probe(name, df, schema)
        rendered = out.compact_dict(top_n) if compact else out.to_dict()
        return {"csv": str(path), "schema": schema.to_dict(), "output": rendered}

    def _cmd_playbook(
        self,
        csv: str,
        config_path: str | None,
        full: bool,
        top_n: int,
    ) -> dict[str, Any]:
        path = Path(csv).expanduser()
        df = load_csv(path)
        schema = self._resolve_schema(df, config_path)
        outputs = run_playbook(df, schema)
        schema_view = (
            schema.to_dict() if full else self._compact_schema(schema)
        )
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
                "Compact view: top-N findings per probe by |magnitude|, no "
                "`evidence` dicts. Call `capelle-cube probe <name> --csv <X>` "
                "to get the full output (all findings + structured evidence) "
                "for one probe — use this when you need values for charts or "
                "tables."
            )
        return payload

    @staticmethod
    def _compact_schema(schema: CubeSchema) -> dict[str, Any]:
        return {
            "time_col": schema.time_col,
            "dim_cols": list(schema.dim_cols),
            "additive_metric_cols": list(schema.additive_metric_cols),
            "ratio_metric_pairs": [list(p) for p in schema.ratio_metric_pairs],
            "client_col": schema.client_col,
            "price_unit_col": schema.price_unit_col,
            "bin_cols": [c for c, _, _ in schema.bin_cols],
        }


def main(argv: list[str] | None = None) -> int:
    return CubeCLI().run(argv)


if __name__ == "__main__":
    sys.exit(main())
