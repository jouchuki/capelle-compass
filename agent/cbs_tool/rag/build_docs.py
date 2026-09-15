"""
Build rich text documents from enriched table metadata for embedding.
One document per series (editions collapsed), not per annual table.
Large series are chunked on the measures list so no information is dropped.
Each chunk carries the full header (title, period, geo, dims) for context.
"""
from ..catalog import _normalize_title, _start_year_str

# text-embedding-3-small: 8192 token limit ≈ ~30k chars.
# Stay comfortably under with a per-chunk char budget.
CHUNK_CHAR_LIMIT = 6000
# Measures per chunk — keeps chunks semantically coherent, not just byte-sliced.
MEASURES_PER_CHUNK = 60


def _build_header(title: str, period: str, freq: str, geo: list[str], dims: list[dict], desc: str) -> str:
    parts = [f"Title: {title}"]
    if period or freq or geo:
        parts.append(f"Period: {period} | Frequency: {freq} | Geo: {', '.join(geo)}")
    non_geo = [d for d in dims if d["type"] not in ("GeoDetail", "GeoDimension")]
    if non_geo:
        parts.append("Dimensions: " + ", ".join(d["title"] for d in non_geo))
    if desc:
        parts.append("Description: " + desc[:400])
    return "\n".join(parts)


def build_series_chunks(tables: list[dict]) -> list[tuple[str, dict]]:
    """
    Build one or more (document, metadata) pairs for a series.
    Returns a list — most series produce one chunk; large ones produce 2-3.
    Each chunk gets the same metadata with a chunk_index field.
    ChromaDB IDs are  latest_id  for chunk 0,  latest_id_c1  for subsequent chunks.
    """
    if not tables:
        raise ValueError("Empty series")

    latest = sorted(tables, key=lambda t: _start_year_str(t.get("Period", "")))[-1]
    series_base = _normalize_title(latest.get("Title", ""))

    # Union of all topics across editions (preserves order: latest first, then older-only)
    seen_titles: set[str] = set()
    all_topics: list[dict] = []
    for t in sorted(tables, key=lambda t: _start_year_str(t.get("Period", "")), reverse=True):
        for topic in t.get("topics", []):
            key = topic["title"].lower()
            if key not in seen_titles:
                seen_titles.add(key)
                all_topics.append(topic)

    dims = latest.get("dimensions", [])

    from ..catalog import _end_year_str
    start_years = [_start_year_str(t.get("Period", "")) for t in tables]
    end_years = [_end_year_str(t.get("Period", "")) for t in tables]
    all_years = [y for y in start_years + end_years if y > 0]
    year_range = f"{min(all_years)}–{max(all_years)}" if all_years else latest.get("Period", "")

    geo_levels: set[str] = set()
    for t in tables:
        geo_levels.update(t.get("geo_levels", []))
    geo_order = ["national", "province", "municipality", "wijk", "buurt"]
    geo_sorted = [g for g in geo_order if g in geo_levels]

    short_desc = (latest.get("ShortDescription") or "").strip()
    summary    = (latest.get("Summary") or "").strip()
    desc = short_desc or summary

    header = _build_header(series_base, year_range, latest.get("Frequency", ""), geo_sorted, dims, desc)

    # Build measure strings
    measure_strs = []
    for t in all_topics:
        s = t["title"]
        if t.get("unit"):
            s += f" ({t['unit']})"
        measure_strs.append(s)

    # Shared metadata
    base_meta = {
        "series_base":       series_base,
        "latest_id":         latest["Identifier"],
        "edition_ids":       ",".join(t["Identifier"] for t in tables),
        "year_min":          min(all_years) if all_years else 0,
        "year_max":          max(all_years) if all_years else 0,
        "geo_levels":        ",".join(geo_sorted),
        "has_buurt":         "buurt" in geo_levels,
        "has_municipality":  "municipality" in geo_levels,
        "frequency":         latest.get("Frequency", ""),
    }

    # Chunk measures — only split when the single doc would exceed the limit
    single_doc = header + "\nMeasures: " + ", ".join(measure_strs) if measure_strs else header
    if len(single_doc) <= CHUNK_CHAR_LIMIT or not measure_strs:
        return [(single_doc, {**base_meta, "chunk_index": 0, "chunk_total": 1})]

    chunks = []
    for i in range(0, len(measure_strs), MEASURES_PER_CHUNK):
        slice_ = measure_strs[i:i + MEASURES_PER_CHUNK]
        doc = header + "\nMeasures: " + ", ".join(slice_)
        chunks.append(doc)

    total = len(chunks)
    result = []
    for i, doc in enumerate(chunks):
        chunk_id = latest["Identifier"] if i == 0 else f"{latest['Identifier']}_c{i}"
        result.append((doc, {**base_meta, "chunk_index": i, "chunk_total": total, "chunk_id": chunk_id}))
    return result


def group_by_series(tables: list[dict]) -> dict[str, list[dict]]:
    """Group enriched tables by series base title."""
    groups: dict[str, list[dict]] = {}
    for t in tables:
        base = _normalize_title(t.get("Title", "")) or t["Identifier"]
        groups.setdefault(base, []).append(t)
    return groups
