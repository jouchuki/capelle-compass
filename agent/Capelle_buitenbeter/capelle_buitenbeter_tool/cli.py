from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent


def _run_python(script: str) -> None:
    subprocess.run([sys.executable, str(ROOT / script)], check=True, cwd=ROOT)


def cmd_run(_: argparse.Namespace) -> None:
    _run_python("run_reports.py")


def cmd_config(_: argparse.Namespace) -> None:
    cfg = {}
    exec((ROOT / "config.py").read_text(encoding="utf-8"), cfg)
    keys = ["MUNICIPALITY", "OUTPUT_DIR", "CSV_PATH"]
    print(json.dumps({k: cfg.get(k) for k in keys}, ensure_ascii=False, indent=2))


def cmd_open_output(_: argparse.Namespace) -> None:
    output_dir = ROOT / "output"
    print(json.dumps({
        "output_dir": str(output_dir),
        "dashboard": str(output_dir / "reports_dashboard.html"),
        "csv": str(output_dir / "reports.csv"),
        "exists": output_dir.exists(),
    }, ensure_ascii=False, indent=2))


def cmd_query(args: argparse.Namespace) -> None:
    """Query citizen reports from CSV."""
    csv_path = ROOT / "output" / "reports.csv"
    if not csv_path.exists():
        print(json.dumps({"error": "reports.csv not found. Run: capelle-buitenbeter run"}))
        return

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Apply filters
    if args.category:
        rows = [r for r in rows if args.category.lower() in r.get("category", "").lower()]
    if args.status:
        rows = [r for r in rows if args.status.lower() in r.get("status", "").lower()]
    if args.open_only:
        rows = [r for r in rows if r.get("is_open", "").lower() == "true"]

    if args.limit:
        rows = rows[:args.limit]

    if args.output_json:
        sys.path.insert(0, str(REPO_ROOT))
        from capelle_rag.output_schema import ToolOutput, ColumnDef, ChartHint
        tool_out = ToolOutput(
            tool="buitenbeter",
            query="capelle-buitenbeter query" + (f" --category {args.category}" if args.category else ""),
            result_type="table",
            data=rows,
            columns=[
                ColumnDef(key="id", label="ID", type="string"),
                ColumnDef(key="category", label="Categorie", type="string"),
                ColumnDef(key="status", label="Status", type="string"),
                ColumnDef(key="lat", label="Latitude", type="number"),
                ColumnDef(key="lon", label="Longitude", type="number"),
                ColumnDef(key="first_seen", label="Eerste melding", type="string"),
                ColumnDef(key="is_open", label="Open", type="string"),
            ],
            metadata={"total_reports": len(rows)},
            chart_hints=[
                ChartHint(type="bar", x="category", y="id", title="Meldingen per categorie"),
                ChartHint(type="map", x="lat", y="lon", title="Meldingen op kaart"),
            ],
        )
        print(tool_out.to_json())
    else:
        print(json.dumps({"count": len(rows), "data": rows}, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="capelle-buitenbeter", description="Capelle BuitenBeter reports CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Fetch reports, update CSV, and rebuild dashboard")
    p_run.set_defaults(func=cmd_run)

    p_cfg = sub.add_parser("config", help="Show current repo config")
    p_cfg.set_defaults(func=cmd_config)

    p_out = sub.add_parser("open-output", help="Print likely output locations")
    p_out.set_defaults(func=cmd_open_output)

    p_query = sub.add_parser("query", help="Query citizen reports")
    p_query.add_argument("--category", help="Filter by category (partial match)")
    p_query.add_argument("--status", help="Filter by status (partial match)")
    p_query.add_argument("--open-only", action="store_true", help="Only open reports")
    p_query.add_argument("--limit", type=int, help="Max results")
    p_query.add_argument("--output-json", action="store_true", help="Emit standardized ToolOutput JSON")
    p_query.set_defaults(func=cmd_query)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
