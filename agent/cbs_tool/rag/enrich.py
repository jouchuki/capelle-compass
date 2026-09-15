"""
Enrich catalog with DataProperties (Topics + Dimensions) for all tables.
Saves to enriched_catalog.jsonl — one JSON line per table.
Resumable: skips tables already in output file.
Run: python -m cbs_tool.rag.enrich [--limit N]
"""
import json
import sys
import time
from pathlib import Path

import cbsodata

from ..catalog import catalog_url_for, load_catalog
from .paths import ENRICHED_PATH, CBS_DATA_DIR


def _fetch_properties(table_id: str) -> dict:
    cat_url = catalog_url_for(table_id)
    try:
        dp = cbsodata.get_meta(table_id, "DataProperties", catalog_url=cat_url)
    except Exception as e:
        return {"error": str(e)}

    topics     = []
    dimensions = []

    for row in dp:
        t = row.get("Type", "")
        if t == "Topic":
            topics.append({
                "key":   row.get("Key", ""),
                "title": row.get("Title", ""),
                "unit":  row.get("Unit", ""),
                "desc":  (row.get("Description") or "").strip()[:200],
            })
        elif t in ("Dimension", "TimeDimension", "GeoDimension", "GeoDetail"):
            dimensions.append({
                "key":   row.get("Key", ""),
                "title": row.get("Title", ""),
                "type":  t,
            })

    return {"topics": topics, "dimensions": dimensions}


def enrich(limit: int | None = None, verbose: bool = True) -> Path:
    tables = load_catalog()

    # Load already-done IDs
    done: set[str] = set()
    if ENRICHED_PATH.exists():
        with open(ENRICHED_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["Identifier"])
                except Exception:
                    pass
        if verbose:
            print(f"Resuming — {len(done)} already enriched", flush=True)

    todo = [t for t in tables if t["Identifier"] not in done]
    if limit:
        todo = todo[:limit]

    CBS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(ENRICHED_PATH, "a", encoding="utf-8") as f:
        for i, table in enumerate(todo):
            tid = table["Identifier"]
            props = _fetch_properties(tid)
            record = {**table, **props}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()

            if verbose and i % 50 == 0:
                print(f"  {i+1}/{len(todo)}  {tid}", flush=True)

            time.sleep(0.25)

    total = len(done) + len(todo)
    if verbose:
        print(f"Done — {total} tables enriched → {ENRICHED_PATH}")
    return ENRICHED_PATH


def load_enriched() -> list[dict]:
    if not ENRICHED_PATH.exists():
        raise FileNotFoundError(
            f"Enriched catalog not found at {ENRICHED_PATH}. "
            "Run: python -m cbs_tool.rag.enrich"
        )
    tables = []
    with open(ENRICHED_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                tables.append(json.loads(line))
    return tables


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    enrich(limit=limit)
