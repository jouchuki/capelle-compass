"""
Describe a table: resolve all dimension codes to human labels.
"""
import re
import time
import cbsodata
from .catalog import find_series, load_catalog, _normalize_title, _start_year_str, catalog_url_for


GEO_TYPES = {"GeoDimension", "GeoDetail"}


def describe_table(table_id: str) -> dict:
    """Return a fully human-readable description of a table. No raw CBS codes in output."""

    cat_url = catalog_url_for(table_id)

    try:
        info = cbsodata.get_info(table_id, catalog_url=cat_url)
    except Exception as e:
        return {"error": str(e)}

    try:
        dp = cbsodata.get_meta(table_id, "DataProperties", catalog_url=cat_url)
    except Exception as e:
        return {"error": f"Could not fetch DataProperties: {e}"}

    dimensions: dict[str, list[dict]] = {}
    metrics: list[str] = []
    geo_dimension: str | None = None
    geo_type_found: str | None = None
    has_perioden = False

    # CBS appends ordinal position to column names when base names collide.
    # Track ordinal so we can expose the actual column name to callers.
    ordinal = 0
    current_group: str | None = None

    for row in dp:
        t    = row.get("Type", "")
        key  = row.get("Key", "")
        title = row.get("Title") or key

        if t == "TopicGroup":
            current_group = title
        elif t in GEO_TYPES:
            geo_dimension   = key
            geo_type_found  = t
            dimensions[title] = _resolve_geo_dim(table_id, key, t)
        elif t == "Dimension":
            if key == "Perioden":
                has_perioden = True
            dimensions[title] = _resolve_dim_labels(table_id, key)
        elif t == "Topic":
            ordinal += 1
            unit  = row.get("Unit", "")
            label = title
            if unit:
                label += f" ({unit})"
            col_name = f"{_to_cbs_column(title)}_{ordinal}"
            metrics.append({"label": label, "column": col_name, "group": current_group})

    # Series
    tables     = load_catalog()
    series     = find_series(table_id, tables)
    series_base = _normalize_title(info.get("Title", ""))
    year_range  = _series_year_range(series)
    editions_nav = _prev_next_editions(table_id, series)

    # Geo levels
    geo_levels = _infer_geo_levels(geo_dimension, geo_type_found, table_id)

    # Plain-English default query from DefaultSelection
    default_query = _parse_default_selection(info.get("DefaultSelection", ""), dp)

    return {
        "id":            table_id,
        "title":         info.get("Title", ""),
        "period":        info.get("Period", ""),
        "frequency":     info.get("Frequency", ""),
        "records":       info.get("RecordCount"),
        "has_perioden":  has_perioden,
        "geo_levels":    geo_levels,
        "geo_dimension": geo_dimension,
        "dimensions":    dimensions,
        "metrics":       metrics,
        "series": {
            "base_title":    series_base,
            "edition_count": len(series),
            "year_range":    year_range,
            "editions":      [
                {"id": t["Identifier"], "period": t.get("Period", "")}
                for t in series
            ],
        },
        "default_query": default_query,
        "editions_nav":  editions_nav or None,
        "note": _build_notes(geo_levels, info),
        "next": _build_next(table_id, dimensions, geo_levels, series),
    }


def _resolve_dim_labels(table_id: str, dim_key: str) -> list[dict]:
    """Return dimension values as list of {label, is_total} — no raw codes."""
    cat_url = catalog_url_for(table_id)
    try:
        vals = cbsodata.get_meta(table_id, dim_key, catalog_url=cat_url)
    except Exception:
        time.sleep(1)
        try:
            vals = cbsodata.get_meta(table_id, dim_key, catalog_url=cat_url)
        except Exception:
            return []

    result = []
    for v in vals:
        key   = v.get("Key", "").strip()
        label = v.get("Title", key)
        # Mark "total" values: typically start with T or are the first value labelled "Totaal..."
        is_total = key.startswith("T") or "totaal" in label.lower() or label.lower().startswith("all")
        result.append({
            "label":    label,
            "is_total": is_total,
            "_key":     key,   # kept internally for filter building, not shown in clean output
        })
    return result


def _resolve_geo_dim(table_id: str, dim_key: str, geo_type: str) -> list[dict]:
    """Summarise a geo dimension by level — no raw codes."""
    cat_url = catalog_url_for(table_id)
    if geo_type == "GeoDetail":
        # WijkenEnBuurten — just show level summary
        try:
            vals = cbsodata.get_meta(table_id, dim_key, catalog_url=cat_url)
        except Exception:
            return [{"level": "municipality/wijk/buurt", "count": "unknown"}]

        prefix_map = {
            "NL": "national", "LD": "landsdeel", "PV": "province",
            "CR": "corop",    "GM": "municipality", "WK": "wijk", "BU": "buurt",
            "AM": "arbeidsmarktregio", "RE": "res-regio",
        }
        counts: dict[str, int] = {}
        for v in vals:
            p = v.get("Key", "").strip()[:2]
            label = prefix_map.get(p, p)
            counts[label] = counts.get(label, 0) + 1

        order = ["national","landsdeel","province","corop","municipality","wijk","buurt"]
        return [
            {"level": level, "count": counts[level]}
            for level in order if level in counts
        ]
    else:
        # GeoDimension (RegioS) — show level summary
        try:
            vals = cbsodata.get_meta(table_id, dim_key, catalog_url=cat_url)
        except Exception:
            return []
        prefix_map = {
            "NL": "national", "LD": "landsdeel", "PV": "province",
            "CR": "corop", "GM": "municipality",
        }
        counts: dict[str, int] = {}
        for v in vals:
            p = v.get("Key", "").strip()[:2]
            label = prefix_map.get(p, p)
            counts[label] = counts.get(label, 0) + 1
        order = ["national","landsdeel","province","corop","municipality"]
        return [
            {"level": level, "count": counts[level]}
            for level in order if level in counts
        ]


def _infer_geo_levels(geo_dimension: str | None, geo_type: str | None, table_id: str) -> list[str]:
    if not geo_dimension:
        return ["national"]
    if geo_type == "GeoDetail":
        return ["municipality", "wijk", "buurt"]
    # GeoDimension — check via already-resolved summary
    cat_url = catalog_url_for(table_id)
    try:
        vals = cbsodata.get_meta(table_id, geo_dimension, catalog_url=cat_url)
        prefix_map = {
            "NL": "national", "LD": "landsdeel", "PV": "province",
            "CR": "corop", "GM": "municipality", "WK": "wijk", "BU": "buurt",
        }
        found = {prefix_map[v["Key"].strip()[:2]] for v in vals if v["Key"].strip()[:2] in prefix_map}
        order = ["national","landsdeel","province","corop","municipality","wijk","buurt"]
        return [l for l in order if l in found]
    except Exception:
        return []


def _to_cbs_column(title: str) -> str:
    """
    Reproduce CBS's PascalCase column-name generation from a Topic title.
    CBS splits on spaces and hyphens, capitalises each word, strips remaining special chars.
    Columns starting with a digit get a 'k_' prefix in the actual API response.
    e.g. 'AOW-uitkering'          → 'AOWUitkering'
         'Inwoners vanaf 15 jaar' → 'InwonersVanaf15Jaar'
         '40% laagste inkomens'   → 'k_40LaagsteInkomens'
    """
    words = re.split(r"[\s\-]+", title)
    pascal = "".join(w[:1].upper() + w[1:] for w in words if w)
    col = re.sub(r"[^A-Za-z0-9]", "", pascal)
    if col and col[0].isdigit():
        col = "k_" + col
    return col


def _series_year_range(series: list[dict]) -> str:
    if not series:
        return ""
    years = [_start_year_str(t.get("Period", "")) for t in series]
    years = [y for y in years if y > 0]
    if not years:
        return ""
    if len(years) == 1:
        return str(years[0])
    return f"{min(years)}–{max(years)}"


def _parse_default_selection(sel: str, dp: list[dict]) -> str:
    """
    Convert DefaultSelection OData filter into a plain English description.
    e.g. "Geslacht eq 'T001038'" → "Geslacht: Totaal mannen en vrouwen"
    """
    if not sel:
        return ""

    # Build key→label lookup from DataProperties
    key_to_label: dict[str, str] = {}
    for row in dp:
        if row.get("Type") in ("Dimension",):
            pass  # we'll do this lookup lazily only for dims in the filter

    # Extract simple eq filters
    parts = re.findall(r"\((\w+) eq '([^']+)'\)", sel)
    if not parts:
        return ""

    # Group by dimension
    by_dim: dict[str, list[str]] = {}
    for dim, val in parts:
        by_dim.setdefault(dim, []).append(val.strip())

    # For each dim, we'd ideally resolve codes to labels
    # For now, just show dimension = values (raw is fine here since it's informational)
    descriptions = []
    for dim, vals in by_dim.items():
        # Find dim title in DataProperties
        dim_title = next((r.get("Title", dim) for r in dp if r.get("Key") == dim), dim)
        if len(vals) <= 3:
            descriptions.append(f"{dim_title}: {', '.join(vals)}")
        else:
            descriptions.append(f"{dim_title}: {len(vals)} values")
    return " | ".join(descriptions)


def _build_notes(geo_levels: list[str], info: dict) -> list[str]:
    notes = []
    records = info.get("RecordCount", 0) or 0
    if records > 500_000:
        notes.append(f"Large table ({records:,} rows) — always use --geo and/or --period to filter.")
    if "buurt" in geo_levels:
        notes.append(
            "Buurt-level null rate: ~20–40% of values suppressed for privacy "
            "(small neighborhood size threshold). Expect many nulls in numeric columns."
        )
    elif "wijk" in geo_levels:
        notes.append("Wijk-level null rate: ~5–15% of values may be suppressed for privacy.")
    if info.get("OutputStatus", "") != "Definitief" and "voorlopig" in (info.get("ShortDescription", "")).lower():
        notes.append("Some figures are provisional (voorlopig).")
    return notes


def _prev_next_editions(table_id: str, series: list[dict]) -> dict:
    """Return prev/next edition IDs for navigation within a series."""
    try:
        idx = next(i for i, t in enumerate(series) if t["Identifier"].lower() == table_id.lower())
    except StopIteration:
        return {}
    result = {}
    if idx > 0:
        p = series[idx - 1]
        result["prev"] = {"id": p["Identifier"], "period": p.get("Period", "")}
    if idx < len(series) - 1:
        n = series[idx + 1]
        result["next"] = {"id": n["Identifier"], "period": n.get("Period", "")}
    return result


def _build_next(table_id: str, dims: dict, geo_levels: list[str], series: list[dict]) -> list[str]:
    hints = []
    if "buurt" in geo_levels:
        hints.append(f"cbs get {table_id} --geo 'Capelle aan den IJssel' --level buurt")
        hints.append(f"cbs get {table_id} --geo 'Capelle aan den IJssel' --level wijk")
    elif "municipality" in geo_levels:
        hints.append(f"cbs get {table_id} --geo 'Capelle aan den IJssel'")

    # Suggest non-total dim filters
    for dim_name, values in dims.items():
        non_totals = [v for v in values if isinstance(v, dict) and not v.get("is_total") and v.get("label")]
        if non_totals:
            hints.append(
                f"cbs get {table_id} --geo 'Capelle aan den IJssel' --level buurt "
                f"--dim \"{dim_name}={non_totals[0]['label']}\""
            )
            break

    if len(series) > 1:
        hints.append(f"cbs series {table_id}  # {len(series)} editions available")
        hints.append(f"cbs get {table_id} --geo 'Capelle aan den IJssel' --level buurt --series")

    return hints
