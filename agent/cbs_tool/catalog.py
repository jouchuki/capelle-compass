"""
Catalog: load, search, group CBS tables into series.
"""
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

_DATA_DIR = Path(os.environ.get("CBS_DATA_DIR", str(Path(__file__).resolve().parents[1] / "reference" / "cbs")))
BUILT_CATALOG    = _DATA_DIR / "catalog.json"
MUNICIPALITIES_F = _DATA_DIR / "municipalities.json"
# Fallback for dev: repo-relative cbs_data/ (editable install, pre-build)
DEFAULT_CATALOG  = Path(__file__).parent.parent / "cbs_data" / "cbs_tables_info.json"
GEO_ENRICHED     = Path(__file__).parent.parent / "cbs_data" / "relevant_tables_municipality.json"

# All known CBS OData catalogs.  opendata.cbs.nl is the cbsodata default
# (catalog_url=None), so we only store the URL for non-default catalogs.
CBS_CATALOGS: list[dict] = [
    {"url": None,                "label": "CBS OpenData"},
    {"url": "dataderden.cbs.nl", "label": "CBS DataDerden"},
]


def _catalog_path() -> Path:
    env = os.environ.get("CBS_CATALOG")
    if env:
        return Path(env)
    if BUILT_CATALOG.exists():
        return BUILT_CATALOG
    return DEFAULT_CATALOG


# ─── Loading ──────────────────────────────────────────────────────────────────

def load_catalog() -> list[dict]:
    path = _catalog_path()
    with open(path, encoding="utf-8") as f:
        tables = json.load(f)

    # Merge geo enrichment if available and catalog isn't already enriched
    if not tables[0].get("geo_levels") and GEO_ENRICHED.exists():
        geo_index: dict[str, dict] = {}
        with open(GEO_ENRICHED, encoding="utf-8") as f:
            for t in json.load(f):
                geo_index[t["identifier"]] = t
        for t in tables:
            ident = t.get("Identifier", "")
            if ident in geo_index:
                g = geo_index[ident]
                t["geo_levels"]       = g.get("geo_levels", [])
                t["has_municipality"] = g.get("has_municipality", False)
                t["geo_dimension"]    = g.get("geo_dimension")
            else:
                _infer_geo_fast(t)
    else:
        for t in tables:
            if not t.get("geo_levels"):
                _infer_geo_fast(t)

    return tables


# ─── Catalog URL lookup ──────────────────────────────────────────────────────

_CATALOG_URL_INDEX: dict[str, str | None] = {}


def catalog_url_for(table_id: str) -> str | None:
    """Return the catalog_url for a table (None = default opendata.cbs.nl)."""
    if not _CATALOG_URL_INDEX:
        for t in load_catalog():
            _CATALOG_URL_INDEX[t.get("Identifier", "")] = t.get("_catalog_url")
    return _CATALOG_URL_INDEX.get(table_id)


def _infer_geo_fast(t: dict):
    """
    Infer geo levels from DefaultPresentation/DefaultSelection without API call.
    Not perfect but catches the majority of cases.
    """
    presentation = t.get("DefaultPresentation", "") + t.get("DefaultSelection", "")
    levels = []
    if "WijkenEnBuurten" in presentation:
        levels = ["municipality", "wijk", "buurt"]
    elif "RegioS" in presentation:
        sel = t.get("DefaultSelection", "")
        if "substringof('GM'" in sel or "substringof('BU'" in sel or "startswith" in sel:
            levels = ["municipality"]
        elif "'PV" in sel or "substringof('PV'" in sel:
            levels = ["national", "province", "municipality"]
        else:
            levels = ["national"]
    elif "Gemeenten" in t.get("Title", "") or "gemeenten" in t.get("Title", ""):
        levels = ["municipality"]
    t["geo_levels"]       = levels
    t["has_municipality"] = "municipality" in levels
    t["geo_dimension"]    = (
        "WijkenEnBuurten" if "WijkenEnBuurten" in presentation
        else "RegioS" if "RegioS" in presentation
        else None
    )


# ─── Search ───────────────────────────────────────────────────────────────────

from .concepts import CONCEPT_MAP

CONCEPT_ALIASES: dict[str, list[str]] = {
    concept: [concept] + data.get("aliases", []) + data.get("search_terms", [])
    for concept, data in CONCEPT_MAP.items()
}


def _expand_query(query: str) -> list[str]:
    """Expand query with concept aliases."""
    terms = query.lower().split()
    expanded = set(terms)
    for concept, aliases in CONCEPT_ALIASES.items():
        for alias in aliases:
            if alias in query.lower():
                expanded.update(aliases)
                break
    return list(expanded)


def search(
    query: str,
    tables: list[dict],
    geo_level: Optional[str] = None,
    after: Optional[int] = None,
    limit: int = 10,
    group_series: bool = True,
) -> list[dict]:
    """
    Full-text search with concept alias expansion.
    By default groups results by series (one entry per series, not per annual edition).
    """
    terms = _expand_query(query)

    def score(t: dict) -> int:
        title = (t.get("Title", "") + " " + t.get("ShortTitle", "")).lower()
        desc  = (t.get("ShortDescription", "") + " " + t.get("Summary", "")).lower()
        s = sum(3 for term in terms if term in title)
        s += sum(1 for term in terms if term in desc)
        return s

    results = [t for t in tables if score(t) > 0]

    if geo_level:
        results = [t for t in results if geo_level in t.get("geo_levels", [])]

    if after:
        def _year(t):
            m = re.search(r"\b(19|20)\d{2}\b", t.get("Period", ""))
            return int(m.group()) if m else 0
        results = [t for t in results if _year(t) >= after]

    results.sort(key=score, reverse=True)

    if group_series:
        return _group_into_series(results, limit)

    return results[:limit]


def _group_into_series(tables: list[dict], limit: int) -> list[dict]:
    """Collapse annual editions into one series entry."""
    seen: dict[str, dict] = {}
    order: list[str] = []

    for t in tables:
        base = _normalize_title(t.get("Title", ""))
        if not base:
            base = t.get("Identifier", "")

        if base not in seen:
            seen[base] = {
                "series_title":  base,
                "editions":      [],
                "geo_levels":    t.get("geo_levels", []),
                "latest_id":     t.get("Identifier"),
                "latest_period": t.get("Period", ""),
                "frequency":     t.get("Frequency", ""),
                "summary":       (t.get("Summary") or "").strip()[:200],
            }
            order.append(base)

        seen[base]["editions"].append({
            "id":     t.get("Identifier"),
            "period": t.get("Period", ""),
        })

    result = []
    for base in order:
        s = seen[base]
        editions = s["editions"]
        # Sort editions by period year
        editions.sort(key=lambda e: _start_year_str(e["period"]))
        s["latest_id"]     = editions[-1]["id"]
        s["latest_period"] = editions[-1]["period"]
        s["year_range"]    = f"{_start_year_str(editions[0]['period'])}–{_start_year_str(editions[-1]['period'])}" if len(editions) > 1 else editions[0]["period"]
        s["edition_count"] = len(editions)
        result.append(s)
        if len(result) >= limit:
            break

    return result


# ─── Series ───────────────────────────────────────────────────────────────────

def _normalize_title(title: str) -> str:
    cleaned = re.sub(r"[,;]?\s*(1[89]\d{2}|20\d{2})\s*$", "", title).strip()
    cleaned = re.sub(r"\s*\(regio-indeling \d{4}\)\s*$", "", cleaned).strip()
    # Also strip trailing year in parentheses
    cleaned = re.sub(r",?\s*\d{4}$", "", cleaned).strip()
    return cleaned


def _start_year_str(period: str) -> int:
    m = re.search(r"\b(1[89]\d{2}|20\d{2})\b", period)
    return int(m.group()) if m else 0


def _end_year_str(period: str) -> int:
    """Extract the last (most recent) year from a period string like '2012-2025'."""
    matches = list(re.finditer(r"\b(1[89]\d{2}|20\d{2})\b", period))
    return int(matches[-1].group()) if matches else 0


def find_series(table_id: str, tables: list[dict]) -> list[dict]:
    """Find all annual editions of the same series as table_id."""
    target = next((t for t in tables if t["Identifier"].lower() == table_id.lower()), None)
    if not target:
        return []

    base = _normalize_title(target.get("Title", ""))
    if not base:
        return [target]

    matches = [t for t in tables if _normalize_title(t.get("Title", "")) == base]
    matches.sort(key=lambda t: _start_year_str(t.get("Period", "")))
    return matches


# ─── Catalog build ────────────────────────────────────────────────────────────

def build_catalog(geo_enrich: bool = False, verbose: bool = True) -> Path:
    """
    Fetch full CBS table list from all known catalogs, infer geo levels,
    save as catalog.json.
    geo_enrich=True does live API calls per table (accurate but slow).
    geo_enrich=False uses fast DefaultPresentation heuristic (recommended).
    """
    import cbsodata

    tables: list[dict] = []
    for cat in CBS_CATALOGS:
        label = cat["label"]
        url   = cat["url"]
        if verbose:
            print(f"Fetching table list from {label}...")
        batch = cbsodata.get_table_list(catalog_url=url)
        # Tag non-default catalogs so downstream calls use the right endpoint
        if url:
            for t in batch:
                t["_catalog_url"] = url
        tables.extend(batch)
        if verbose:
            print(f"  {len(batch)} tables from {label}")

    if verbose:
        print(f"  {len(tables)} tables total")

    for t in tables:
        if geo_enrich:
            _geo_enrich_live(t, verbose)
        else:
            _infer_geo_fast(t)

    # Build municipalities lookup while we have all tables
    _build_municipalities(tables)

    BUILT_CATALOG.parent.mkdir(parents=True, exist_ok=True)
    with open(BUILT_CATALOG, "w", encoding="utf-8") as f:
        json.dump(tables, f, ensure_ascii=False, indent=2)

    if verbose:
        print(f"Saved → {BUILT_CATALOG}")
    return BUILT_CATALOG


def _geo_enrich_live(t: dict, verbose: bool):
    import cbsodata
    table_id = t.get("Identifier", "")
    cat_url  = t.get("_catalog_url")
    try:
        dp = cbsodata.get_meta(table_id, "DataProperties", catalog_url=cat_url)
        geo_rows = [r for r in dp if r.get("Type") in ("GeoDimension", "GeoDetail")]
        if not geo_rows:
            t["geo_levels"] = ["national"]
            t["geo_dimension"] = None
            return
        geo_key = geo_rows[0]["Key"]
        geo_type = geo_rows[0]["Type"]
        t["geo_dimension"] = geo_key
        if geo_type == "GeoDetail":
            t["geo_levels"] = ["municipality", "wijk", "buurt"]
        else:
            vals = cbsodata.get_meta(table_id, geo_key, catalog_url=cat_url)
            prefix_map = {
                "NL": "national", "LD": "landsdeel", "PV": "province",
                "CR": "corop", "GM": "municipality", "WK": "wijk", "BU": "buurt",
            }
            found = {prefix_map[v["Key"].strip()[:2]] for v in vals if v["Key"].strip()[:2] in prefix_map}
            order = ["national","landsdeel","province","corop","municipality","wijk","buurt"]
            t["geo_levels"] = [l for l in order if l in found]
        t["has_municipality"] = "municipality" in t["geo_levels"]
        time.sleep(0.2)
    except Exception:
        _infer_geo_fast(t)


def _build_municipalities(tables: list[dict]):
    """Build municipality code ↔ name lookup from CBS API, save to municipalities.json."""
    if MUNICIPALITIES_F.exists():
        return
    import cbsodata

    by_name: dict[str, str] = {}  # lowercase name → GM code
    by_code: dict[str, str] = {}  # GM code → display name

    # Find a catalog table with RegioS geo dimension and municipality level
    candidate = next(
        (t for t in tables
         if "municipality" in t.get("geo_levels", [])
         and t.get("geo_dimension") == "RegioS"),
        None,
    )
    if candidate:
        table_id = candidate.get("Identifier", "")
        geo_dim  = candidate.get("geo_dimension", "RegioS")
        cat_url = candidate.get("_catalog_url")
        try:
            vals = cbsodata.get_meta(table_id, geo_dim, catalog_url=cat_url)
            for v in vals:
                code = v.get("Key", "").strip()
                name = v.get("Title", "").strip()
                if code.startswith("GM") and name:
                    by_name[name.lower()] = code
                    by_code[code] = name
        except Exception:
            pass

    # Fallback: extract bare codes from DefaultSelection if API call failed
    if not by_code:
        for t in tables:
            sel = t.get("DefaultSelection", "")
            for m in re.finditer(r"eq '(GM\d{4,6})\s*'", sel):
                code = m.group(1)
                if code not in by_code:
                    by_code[code] = code
                    by_name[code.lower()] = code

    MUNICIPALITIES_F.parent.mkdir(parents=True, exist_ok=True)
    with open(MUNICIPALITIES_F, "w", encoding="utf-8") as f:
        json.dump({"by_name": by_name, "by_code": by_code}, f, ensure_ascii=False, indent=2)
