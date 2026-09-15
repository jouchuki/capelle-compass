"""
python run_reports.py
open output/reports_dashboard.html
"""
import csv, httpx
from datetime import datetime, timezone
from pathlib import Path
from config import MUNICIPALITY, OUTPUT_DIR, CSV_PATH
from reports_visualizer import render_dashboard

BASE_URL = "https://binnenbeter.nl/api/v1"

CATEGORY_MAP = {
    "Wegen en Verkeer":            ("Roads & Traffic",           "Mobility & Infrastructure"),
    "Bestrating":                  ("Pavement & Paving",         "Mobility & Infrastructure"),
    "Straatverlichting (OV)":      ("Street Lighting",           "Mobility & Infrastructure"),
    "Straat en Verkeersborden":    ("Road & Traffic Signs",      "Mobility & Infrastructure"),
    "Verkeerslicht(en)":           ("Traffic Lights",            "Mobility & Infrastructure"),
    "Kabel en Leidingen":          ("Cables & Pipelines",        "Mobility & Infrastructure"),
    "Groen":                       ("Green Space & Trees",       "Sports, Culture & Recreation"),
    "Speel en Sport":              ("Playgrounds & Sports",      "Sports, Culture & Recreation"),
    "Graffiti":                    ("Graffiti",                  "Sports, Culture & Recreation"),
    "Riolering":                   ("Sewage & Drainage",         "Health & Environment"),
    "Watergang":                   ("Waterways & Canals",        "Health & Environment"),
    "Wateroverlast":               ("Water Nuisance / Flooding", "Health & Environment"),
    "Stankoverlast":               ("Odour Nuisance",            "Health & Environment"),
    "Afval":                       ("Waste & Litter",            "Health & Environment"),
    "Overlast dieren":             ("Animal Nuisance",           "Health & Environment"),
    "Civiele objecten + Diversen": ("Civil Objects & Other",     "Governance & Administration"),
    "Bouw- en Woningtoezicht":     ("Building Inspection",       "Housing & Spatial Planning"),
}

FIELDS = ["id", "latitude", "longitude", "status", "category_nl", "category_en",
          "policy_domain", "description", "first_seen", "last_seen", "is_open"]


def fetch() -> list[dict]:
    resp = httpx.get(f"{BASE_URL}/reports/{MUNICIPALITY}", timeout=30)
    resp.raise_for_status()
    raw = resp.json().get("data", {}).get("items", [])
    enriched = []
    for r in raw:
        cat_nl = (r.get("category") or {}).get("code", "Unknown")
        en, domain = CATEGORY_MAP.get(cat_nl, (cat_nl, "Governance & Administration"))
        enriched.append({
            "id":            str(r.get("id")),
            "latitude":      r.get("latitude"),
            "longitude":     r.get("longitude"),
            "status":        r.get("status"),
            "category_nl":   cat_nl,
            "category_en":   en,
            "policy_domain": domain,
        })
    return enriched


def load_csv() -> dict[str, dict]:
    path = Path(CSV_PATH)
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def save_csv(reports: dict[str, dict], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(reports.values())


def snapshot_paths(run_ts: str) -> tuple[Path, Path]:
    stamp = run_ts.replace(":", "-")
    out = Path(OUTPUT_DIR)
    return out / f"reports_{stamp}.csv", out / f"reports_dashboard_{stamp}.html"


def upsert(existing: dict, incoming: list[dict], run_ts: str) -> tuple[dict, dict]:
    incoming_ids = {r["id"] for r in incoming}
    new_count = closed_count = 0

    for r in incoming:
        if r["id"] in existing:
            existing[r["id"]]["status"]    = r["status"]
            existing[r["id"]]["last_seen"] = run_ts
            existing[r["id"]]["is_open"]   = "1"
        else:
            existing[r["id"]] = {**r, "description": "", "first_seen": run_ts, "last_seen": run_ts, "is_open": "1"}
            new_count += 1

    for rid, row in existing.items():
        if row["is_open"] == "1" and rid not in incoming_ids:
            existing[rid]["is_open"]   = "0"
            existing[rid]["last_seen"] = run_ts
            closed_count += 1

    return existing, {"new": new_count, "closed": closed_count, "total_open": len(incoming_ids)}


def main() -> None:
    run_ts   = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[fetch] run_ts={run_ts}")

    incoming = fetch()
    print(f"[fetch] API returned {len(incoming)} reports")

    existing      = load_csv()
    merged, stats = upsert(existing, incoming, run_ts)

    latest_csv, latest_html = Path(CSV_PATH), Path(OUTPUT_DIR) / "reports_dashboard.html"
    snap_csv, snap_html = snapshot_paths(run_ts)

    save_csv(merged, latest_csv)
    save_csv(merged, snap_csv)
    print(f"[csv]   {stats['new']} new  |  {stats['closed']} closed  |  {stats['total_open']} open  →  {latest_csv}")
    print(f"[snap]  csv snapshot → {snap_csv}")

    open_reports = [r for r in merged.values() if r["is_open"] == "1"]
    for r in open_reports:
        r["is_new"] = r["first_seen"] == run_ts

    path = render_dashboard(open_reports, MUNICIPALITY)
    snap_html.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[viz]   dashboard → {latest_html}")
    print(f"[snap]  dashboard snapshot → {snap_html}")
    print("Done!  Run: open output/reports_dashboard.html")


if __name__ == "__main__":
    main()
