from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent


def _run_python(script: str) -> None:
    subprocess.run([sys.executable, str(ROOT / script)], check=True, cwd=ROOT)


def cmd_run(_: argparse.Namespace) -> None:
    _run_python("main.py")


def cmd_config(_: argparse.Namespace) -> None:
    cfg = {}
    exec((ROOT / "config.py").read_text(encoding="utf-8"), cfg)
    keys = [
        "MUNICIPALITY",
        "MUNICIPALITY_SLUG",
        "START_URL",
        "OUTPUT_DIR",
        "BUDGET_LINK_KEYWORDS",
        "AMOUNT_COLUMN_PATTERNS",
        "DEPARTMENTS",
    ]
    print(json.dumps({k: cfg.get(k) for k in keys}, ensure_ascii=False, indent=2))


def cmd_open_output(_: argparse.Namespace) -> None:
    output_dir = ROOT / "output"
    print(json.dumps({
        "output_dir": str(output_dir),
        "dashboard": str(output_dir / "dashboard.html"),
        "run_log": str(output_dir / "run_log.json"),
        "exists": output_dir.exists(),
    }, ensure_ascii=False, indent=2))


DOMAIN_MAP = {
    "0": "Governance & Financiën",
    "1": "Veiligheid",
    "2": "Verkeer & Mobiliteit",
    "3": "Economie",
    "4": "Onderwijs",
    "5": "Cultuur, Sport & Recreatie",
    "6": "Sociaal Domein",
    "7": "Volksgezondheid & Milieu",
    "8": "Ruimtelijke Ordening & Wonen",
}


def cmd_query(args: argparse.Namespace) -> None:
    """Query budget data by domain and/or year."""
    budget_json = ROOT / "output" / "budget.json"
    if not budget_json.exists():
        print(json.dumps({"error": "budget.json not found. Run: capelle-budget run"}))
        return

    rows = json.loads(budget_json.read_text(encoding="utf-8"))

    # Aggregate by domain + year
    totals = defaultdict(lambda: {"lasten": 0.0, "baten": 0.0})
    for r in rows:
        tv = str(r.get("taakveld", ""))
        prefix = tv.split(".")[0] if "." in tv else tv[:1]
        domain = DOMAIN_MAP.get(prefix, "Overig")
        jaar = r.get("jaar")
        if not jaar:
            continue

        # Apply filters
        if args.domain and args.domain.lower() not in domain.lower():
            continue
        if args.year and jaar != args.year:
            continue

        totals[(domain, jaar)]["lasten"] += r.get("lasten_num", 0) or 0
        totals[(domain, jaar)]["baten"] += r.get("baten_num", 0) or 0

    data = []
    for (domain, jaar), v in sorted(totals.items()):
        lasten_k = round(v["lasten"] / 1000)
        baten_k = round(v["baten"] / 1000)
        data.append({
            "domain": domain,
            "year": jaar,
            "lasten_k": lasten_k,
            "baten_k": baten_k,
            "saldo_k": baten_k - lasten_k,
        })

    if args.output_json:
        sys.path.insert(0, str(REPO_ROOT))
        from capelle_rag.output_schema import ToolOutput, ColumnDef, ChartHint
        tool_out = ToolOutput(
            tool="budget",
            query=f"capelle-budget query" + (f" --domain {args.domain}" if args.domain else "") + (f" --year {args.year}" if args.year else ""),
            result_type="time_series",
            data=data,
            columns=[
                ColumnDef(key="domain", label="Beleidsterrein", type="string"),
                ColumnDef(key="year", label="Jaar", type="year"),
                ColumnDef(key="lasten_k", label="Lasten", type="number", unit="€k"),
                ColumnDef(key="baten_k", label="Baten", type="number", unit="€k"),
                ColumnDef(key="saldo_k", label="Saldo", type="number", unit="€k"),
            ],
            metadata={"domain_filter": args.domain, "year_filter": args.year},
            chart_hints=[
                ChartHint(type="bar", x="year", y=["lasten_k", "baten_k"], group_by="domain",
                          title="Capelle Budget per Domein"),
                ChartHint(type="line", x="year", y="saldo_k", group_by="domain",
                          title="Begrotingssaldo per Domein"),
            ],
        )
        print(tool_out.to_json())
    else:
        print(json.dumps({"count": len(data), "data": data}, ensure_ascii=False, indent=2))


def cmd_insights(args: argparse.Namespace) -> None:
    """Show AI-generated budget insights."""
    insights_json = ROOT / "output" / "insights.json"
    if not insights_json.exists():
        print(json.dumps({"error": "insights.json not found. Run: capelle-budget run"}))
        return

    insights = json.loads(insights_json.read_text(encoding="utf-8"))

    if args.output_json:
        sys.path.insert(0, str(REPO_ROOT))
        from capelle_rag.output_schema import ToolOutput, ColumnDef, ChartHint
        data = [{"key": k, "insight": v} for k, v in insights.items() if isinstance(v, str)]
        tool_out = ToolOutput(
            tool="budget",
            query="capelle-budget insights",
            result_type="summary",
            data=data,
            columns=[
                ColumnDef(key="key", label="Aspect", type="string"),
                ColumnDef(key="insight", label="Analyse", type="text_snippet"),
            ],
            metadata={"source": "AI-generated fiscal analysis"},
        )
        print(tool_out.to_json())
    else:
        print(json.dumps(insights, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="capelle-budget", description="Capelle budget CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run the full budget pipeline")
    p_run.set_defaults(func=cmd_run)

    p_cfg = sub.add_parser("config", help="Show current repo config")
    p_cfg.set_defaults(func=cmd_config)

    p_out = sub.add_parser("open-output", help="Print likely output locations")
    p_out.set_defaults(func=cmd_open_output)

    p_query = sub.add_parser("query", help="Query budget data by domain/year")
    p_query.add_argument("--domain", help="Filter by domain name (partial match)")
    p_query.add_argument("--year", type=int, help="Filter by year")
    p_query.add_argument("--output-json", action="store_true", help="Emit standardized ToolOutput JSON")
    p_query.set_defaults(func=cmd_query)

    p_insights = sub.add_parser("insights", help="Show AI budget insights")
    p_insights.add_argument("--output-json", action="store_true", help="Emit standardized ToolOutput JSON")
    p_insights.set_defaults(func=cmd_insights)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
