#!/usr/bin/env python3
"""``capelle-report`` — assemble + validate + write a v2 block-based AnalysisResult.

The ohrs analysis agent pipes a candidate report (JSON) into this CLI; the CLI
validates it against the v2 schema, the chart data-shape catalog, the citation
registry, and the URL policy, then writes it to an absolute path inside the
per-job directory. On any violation it writes NOTHING, prints every error to
stderr, and exits non-zero so the agent self-corrects and retries.

Usage:
    capelle-report --out /abs/path/analysis.json < report.json
    capelle-report --in report.json --out /abs/path/analysis.json

This file is bundled under ``agent/Capelle_report``. The Compass frontend
renders the same schema in ``frontend/src/types/index.ts`` and
``frontend/src/utils/chartContracts.ts``; keep the contracts in sync.

Standards note: this is a CLI, so stdout/stderr ARE the interface — printing is
intentional here, unlike in library code.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Callable

SCHEMA_VERSION = 2

# Mirror of Compass frontend/src/types/index.ts ChartKind.
CHART_KINDS: frozenset[str] = frozenset(
    {
        "bar",
        "grouped_bar",
        "stacked_bar",
        "line",
        "area",
        "pie",
        "donut",
        "scatter",
        "treemap",
        "histogram",
        "boxplot",
        "violin",
    }
)

# Mirror of the frontend Block discriminated-union member tags.
BLOCK_TYPES: frozenset[str] = frozenset(
    {
        "heading",
        "prose",
        "chart",
        "table",
        "kpi",
        "callout",
        "quote",
        "divider",
        "sources",
    }
)

_NUMERIC_COLUMN_TYPES: frozenset[str] = frozenset({"number", "year"})

# Mirror of Compass backend _BELEID_URL_RE — only beleid URLs of this
# exact shape survive; everything else is treated as fabricated.
_BELEID_URL_RE = re.compile(
    r"^https://capelleaandenijssel\.begrotingsapp\.nl/"
    r"(begroting|voorjaarsnota|najaarsnota|jaarstukken|slotwijziging|"
    r"bestuursrapportage|kadernota)-\d{4}(-\d+)?/"
    r"(programma|paragraaf|bestuur|bestanden)/[a-z0-9-]+$"
)


class ReportValidationError(Exception):
    """Raised when the candidate report violates the v2 contract."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} validation error(s)")


class ChartContracts:
    """Per-chart-kind data-shape validators (port of ``chartContracts.ts``).

    Each validator returns ``None`` when the spec's data fits the kind, or a
    human-readable reason string when it does not. A failing chart is not a
    hard report error on its own — the UI falls back to a table — but
    ``capelle-report`` surfaces it as a warning so the agent can pick a better
    kind or supply the right columns.
    """

    def __init__(self, spec: dict[str, Any]) -> None:
        self._spec = spec
        self._columns: list[dict[str, Any]] = spec.get("columns") or []
        self._data: list[dict[str, Any]] = spec.get("data") or []
        self._column_types: dict[str, str] = {
            str(c.get("key")): str(c.get("type", "")) for c in self._columns
        }

    def _y_list(self) -> list[str]:
        y = self._spec.get("y")
        if y is None:
            return []
        if isinstance(y, list):
            return [str(k) for k in y]
        return [str(y)]

    def _has_column(self, key: str | None) -> bool:
        if not key:
            return False
        if key not in self._column_types:
            return False
        return len(self._data) > 0 and key in self._data[0]

    def _is_numeric(self, key: str) -> bool:
        return self._column_types.get(key, "") in _NUMERIC_COLUMN_TYPES

    def _category_value(self) -> str | None:
        x = self._spec.get("x")
        if not self._has_column(x):
            return "x column missing from data"
        ys = self._y_list()
        if not ys:
            return "y column required"
        if not self._has_column(ys[0]):
            return f'y column "{ys[0]}" missing from data'
        if not self._is_numeric(ys[0]):
            return f'y column "{ys[0]}" is not numeric'
        return None

    def _multi_series(self) -> str | None:
        x = self._spec.get("x")
        if not self._has_column(x):
            return "x column missing from data"
        ys = self._y_list()
        if len(ys) < 2:
            return "needs >=2 y series"
        for y in ys:
            if not self._has_column(y):
                return f'y column "{y}" missing from data'
            if not self._is_numeric(y):
                return f'y column "{y}" is not numeric'
        return None

    def _numeric_value(self) -> str | None:
        ys = self._y_list()
        if not ys:
            return "numeric value column required"
        if not self._has_column(ys[0]):
            return f'value column "{ys[0]}" missing from data'
        if not self._is_numeric(ys[0]):
            return f'value column "{ys[0]}" is not numeric'
        return None

    def _xy_numeric(self) -> str | None:
        x = self._spec.get("x")
        if not self._has_column(x) or not self._is_numeric(str(x)):
            return "numeric x required"
        ys = self._y_list()
        if not ys or not self._has_column(ys[0]) or not self._is_numeric(ys[0]):
            return "numeric y required"
        return None

    def validate(self) -> str | None:
        """Return ``None`` if the spec fits its kind, else the failure reason."""
        if len(self._data) == 0:
            return "no data"
        kind = self._spec.get("kind")
        validator = self._VALIDATORS.get(str(kind))
        if validator is None:
            return f'unknown chart kind "{kind}"'
        return validator(self)

    _VALIDATORS: dict[str, Callable[["ChartContracts"], str | None]] = {
        "bar": _category_value,
        "line": _category_value,
        "area": _category_value,
        "pie": _category_value,
        "donut": _category_value,
        "treemap": _category_value,
        "grouped_bar": _multi_series,
        "stacked_bar": _multi_series,
        "histogram": _numeric_value,
        "boxplot": _numeric_value,
        "violin": _numeric_value,
        "scatter": _xy_numeric,
    }


class ReportValidator:
    """Validates a candidate v2 ``AnalysisResult`` document."""

    def __init__(self, report: dict[str, Any]) -> None:
        self._report = report
        self._errors: list[str] = []
        self._warnings: list[str] = []

    @property
    def warnings(self) -> list[str]:
        return self._warnings

    def _err(self, msg: str) -> None:
        self._errors.append(msg)

    def _warn(self, msg: str) -> None:
        self._warnings.append(msg)

    def _citation_ids(self) -> set[str]:
        registry = self._report.get("citations")
        if not isinstance(registry, list):
            return set()
        return {
            str(c.get("id"))
            for c in registry
            if isinstance(c, dict) and c.get("id")
        }

    def _validate_top_level(self) -> None:
        if self._report.get("schema_version") != SCHEMA_VERSION:
            self._err(
                f"schema_version must be {SCHEMA_VERSION}, got "
                f"{self._report.get('schema_version')!r}"
            )
        for field, kind in (
            ("id", str),
            ("timestamp", str),
            ("query", str),
            ("summary", str),
        ):
            value = self._report.get(field)
            if not isinstance(value, kind) or not value:
                self._err(f'required field "{field}" missing or not a non-empty string')
        if not isinstance(self._report.get("blocks"), list):
            self._err('"blocks" must be a list')
        if not isinstance(self._report.get("citations"), list):
            self._err('"citations" must be a list')

    def _validate_citations_registry(self) -> None:
        registry = self._report.get("citations")
        if not isinstance(registry, list):
            return
        for idx, citation in enumerate(registry):
            if not isinstance(citation, dict):
                self._err(f"citations[{idx}] is not an object")
                continue
            for field in ("id", "source", "doc_type", "document"):
                if not citation.get(field):
                    self._err(f'citations[{idx}] missing required field "{field}"')
            source = citation.get("source")
            url = citation.get("source_url")
            if url:
                if source != "beleid":
                    self._err(
                        f"citations[{idx}] ({source}) carries a source_url; only "
                        "beleid citations may have one — omit it"
                    )
                elif not _BELEID_URL_RE.match(str(url)):
                    self._err(
                        f"citations[{idx}] beleid source_url does not match the "
                        f"canonical beleid URL shape: {url!r}"
                    )

    def _referenced_citation_ids(self, block: dict[str, Any]) -> list[str]:
        ids: list[str] = []
        many = block.get("citations")
        if isinstance(many, list):
            ids.extend(str(i) for i in many)
        single = block.get("citation")
        if single:
            ids.append(str(single))
        return ids

    def _validate_blocks(self) -> None:
        blocks = self._report.get("blocks")
        if not isinstance(blocks, list):
            return
        known_ids = self._citation_ids()
        for idx, block in enumerate(blocks):
            if not isinstance(block, dict):
                self._err(f"blocks[{idx}] is not an object")
                continue
            btype = block.get("type")
            if btype not in BLOCK_TYPES:
                self._err(
                    f'blocks[{idx}] has unknown type {btype!r}; '
                    f"allowed: {sorted(BLOCK_TYPES)}"
                )
                continue
            for cid in self._referenced_citation_ids(block):
                if cid not in known_ids:
                    self._err(
                        f"blocks[{idx}] ({btype}) references unknown citation "
                        f"id {cid!r} — add it to the citations registry"
                    )
            if btype == "chart":
                self._validate_chart_block(idx, block)
            elif btype == "table":
                self._validate_table_block(idx, block)

    def _validate_chart_block(self, idx: int, block: dict[str, Any]) -> None:
        spec = block.get("spec")
        if not isinstance(spec, dict):
            self._err(f"blocks[{idx}] chart block missing a spec object")
            return
        if spec.get("kind") not in CHART_KINDS:
            self._err(
                f"blocks[{idx}] chart kind {spec.get('kind')!r} not in catalog; "
                f"allowed: {sorted(CHART_KINDS)}"
            )
            return
        reason = ChartContracts(spec).validate()
        if reason is not None:
            # A contract miss is a warning, not a hard error: the UI degrades
            # to a table. We still surface it so the agent can do better.
            self._warn(
                f"blocks[{idx}] chart kind {spec.get('kind')!r} will render as a "
                f"table (data shape: {reason})"
            )

    def _validate_table_block(self, idx: int, block: dict[str, Any]) -> None:
        if not isinstance(block.get("columns"), list) or not block["columns"]:
            self._err(f"blocks[{idx}] table block needs a non-empty columns list")
        if not isinstance(block.get("data"), list):
            self._err(f"blocks[{idx}] table block needs a data list")

    def validate(self) -> None:
        """Run all checks; raise :class:`ReportValidationError` if any fail."""
        if not isinstance(self._report, dict):
            raise ReportValidationError(["top-level report is not a JSON object"])
        self._validate_top_level()
        self._validate_citations_registry()
        self._validate_blocks()
        if self._errors:
            raise ReportValidationError(self._errors)


def _load_input(path: str | None) -> dict[str, Any]:
    raw = (
        sys.stdin.read()
        if path is None
        else open(path, encoding="utf-8").read()  # noqa: SIM115 — short-lived CLI read
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReportValidationError([f"input is not valid JSON: {exc}"]) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="capelle-report",
        description="Validate + write a v2 block-based AnalysisResult.",
    )
    parser.add_argument(
        "--in",
        dest="in_path",
        default=None,
        help="Read the report JSON from this file (default: stdin).",
    )
    parser.add_argument(
        "--out",
        dest="out_path",
        required=True,
        help="Absolute path to write the validated report to.",
    )
    args = parser.parse_args(argv)

    try:
        report = _load_input(args.in_path)
        validator = ReportValidator(report)
        validator.validate()
    except ReportValidationError as exc:
        print("capelle-report: report rejected — not written.", file=sys.stderr)
        for err in exc.errors:
            print(f"  - {err}", file=sys.stderr)
        print(
            "Fix the issues above and re-run capelle-report.", file=sys.stderr
        )
        return 1

    try:
        with open(args.out_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"capelle-report: could not write {args.out_path}: {exc}", file=sys.stderr)
        return 2

    block_count = len(report.get("blocks") or [])
    print(
        f"capelle-report: wrote {args.out_path} "
        f"({block_count} blocks, schema v{SCHEMA_VERSION})."
    )
    for warning in validator.warnings:
        print(f"capelle-report: warning — {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
