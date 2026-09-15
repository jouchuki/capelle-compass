"""
Fetch data from CBS — single table or across a series.
"""
import re
import time
import cbsodata
import pandas as pd
from typing import Optional

from .catalog import catalog_url_for
from .geo import build_geo_filter, parse_multi_geo, get_geo_name


_KNOWN_GEO_KEYS = {"WijkenEnBuurten", "RegioS", "Gemeenten", "Regio", "Gebieden"}


def _get_geo_dimension(table_id: str) -> tuple[str | None, bool]:
    """Return (geo_dimension_key, has_perioden)."""
    cat_url = catalog_url_for(table_id)
    try:
        dp = cbsodata.get_meta(table_id, "DataProperties", catalog_url=cat_url)
        # Primary: detect by type
        geo_row = next(
            (r for r in dp if r.get("Type") in ("GeoDetail", "GeoDimension")),
            None,
        )
        # Fallback: detect by well-known key names even if type is plain "Dimension"
        if not geo_row:
            geo_row = next(
                (r for r in dp if r.get("Key") in _KNOWN_GEO_KEYS),
                None,
            )
        has_perioden = any(r.get("Key") == "Perioden" for r in dp)
        return (geo_row["Key"] if geo_row else None), has_perioden
    except Exception:
        return None, False


def _resolve_dim_filter(table_id: str, dim_expr: str) -> str:
    """
    Resolve 'DimensionLabel=ValueLabel' to OData filter fragment.
    e.g. 'Geslacht=Mannen' → "Geslacht eq '3000'"
    """
    if "=" not in dim_expr:
        raise ValueError(f"--dim must be 'DimensionName=Label', got: {dim_expr}")

    dim_name, label = [p.strip() for p in dim_expr.split("=", 1)]

    cat_url = catalog_url_for(table_id)
    try:
        dp = cbsodata.get_meta(table_id, "DataProperties", catalog_url=cat_url)
    except Exception:
        raise ValueError(f"Could not fetch DataProperties for {table_id}")

    # Find the dimension key by matching Key or Title
    dim_key = None
    for row in dp:
        if row.get("Type") not in ("Dimension", "GeoDimension"):
            continue
        if (row.get("Key", "").lower() == dim_name.lower() or
                row.get("Title", "").lower() == dim_name.lower()):
            dim_key = row["Key"]
            break

    if not dim_key:
        available_dims = [
            row.get("Title") or row.get("Key")
            for row in dp
            if row.get("Type") in ("Dimension", "GeoDimension")
        ]
        raise ValueError(
            f"Dimension '{dim_name}' not found in {table_id}. "
            f"Available: {available_dims}"
        )

    # Fetch dimension values and find matching label
    try:
        vals = cbsodata.get_meta(table_id, dim_key, catalog_url=cat_url)
    except Exception:
        raise ValueError(f"Could not fetch values for dimension '{dim_key}'")

    match = next(
        (v for v in vals if v.get("Title", "").lower() == label.lower()),
        None,
    )
    if not match:
        available = [v.get("Title", "") for v in vals]
        raise ValueError(
            f"Value '{label}' not found in dimension '{dim_name}'. "
            f"Available: {available[:20]}"
        )

    return f"{dim_key} eq '{match['Key']}'"


def fetch_table(
    table_id: str,
    gm_codes: list[str],
    level: str = "municipality",
    period: Optional[str] = None,
    dim_filters: Optional[list[str]] = None,
    geo_dimension: Optional[str] = None,
    has_perioden: Optional[bool] = None,
    table_year: Optional[int] = None,
) -> pd.DataFrame:
    """Fetch data for one or more municipalities. Returns DataFrame with _geo_name, _gm_code, _year."""

    if geo_dimension is None or has_perioden is None:
        detected_geo, detected_perioden = _get_geo_dimension(table_id)
        geo_dimension = geo_dimension or detected_geo
        if has_perioden is None:
            has_perioden = detected_perioden

    cat_url = catalog_url_for(table_id)

    # Fall back to table-level year if no Perioden dimension
    if not has_perioden and table_year is None:
        try:
            info = cbsodata.get_info(table_id, catalog_url=cat_url)
            m = re.search(r"\b(1[89]\d{2}|20\d{2})\b", info.get("Period", ""))
            table_year = int(m.group()) if m else None
        except Exception:
            pass

    all_frames = []

    targets = gm_codes if gm_codes else [None]

    for gm_code in targets:
        filters = []

        if geo_dimension and gm_code:
            filters.append(build_geo_filter(geo_dimension, gm_code, level))

        if period and has_perioden:
            filters.append(f"Perioden eq '{_normalize_period(period)}'")

        if dim_filters:
            for df_expr in dim_filters:
                filters.append(_resolve_dim_filter(table_id, df_expr))

        odata_filter = " and ".join(filters) if filters else None

        try:
            rows = cbsodata.get_data(table_id, filters=odata_filter, catalog_url=cat_url)
        except Exception as e:
            print(f"  Warning: failed for {gm_code or 'all'}: {e}")
            continue

        if not rows:
            continue

        df = pd.DataFrame(rows)

        # Strip trailing whitespace from all string columns (CBS pads to fixed width)
        for col in df.select_dtypes(include="object").columns:
            df[col] = df[col].str.strip()

        # Add context columns
        df["_gm_code"]  = gm_code or ""
        df["_geo_name"] = get_geo_name(gm_code) if gm_code else ""
        df["_year"]     = _derive_year(df) if has_perioden else pd.array([table_year] * len(df), dtype="Int64")

        all_frames.append(df)
        time.sleep(0.3)

    if not all_frames:
        return pd.DataFrame()

    result = pd.concat(all_frames, ignore_index=True)
    result, dedup_note = _maybe_dedup_quarterly(result)
    if dedup_note:
        result.attrs["dedup_note"] = dedup_note
    return result


def fetch_series(
    series: list[dict],
    gm_codes: list[str],
    level: str = "municipality",
    dim_filters: Optional[list[str]] = None,
    reconcile: bool = True,
) -> pd.DataFrame:
    """
    Fetch all editions in a series and merge into one long-format DataFrame.

    reconcile=True (default): strip CBS ordinal suffixes from column names so
    that NettoArbeidsparticipatie_3 (2013) and NettoArbeidsparticipatie_5 (2024)
    land in the same column. Also applies known historical concept renames.
    reconcile=False: naive pd.concat, columns filled with NaN on mismatch.
    """
    from .reconcile import reconcile_editions

    all_frames: list[pd.DataFrame] = []
    collected_editions: list[dict] = []

    for edition in series:
        table_id   = edition["Identifier"]
        period_str = edition.get("Period", "")

        m    = re.search(r"\b(1[89]\d{2}|20\d{2})\b", period_str)
        year = int(m.group()) if m else None

        geo_dim, has_perioden = _get_geo_dimension(table_id)

        try:
            df = fetch_table(
                table_id,
                gm_codes,
                level=level,
                dim_filters=dim_filters,
                geo_dimension=geo_dim,
                has_perioden=has_perioden,
            )
        except Exception as e:
            print(f"  Skipping {table_id} ({period_str}): {e}")
            continue

        if df.empty:
            print(f"  {table_id} ({period_str}): no data")
            continue

        # Override _year with series-level year if Perioden not present
        if not has_perioden and year:
            df["_year"] = year

        df["_table_id"] = table_id
        all_frames.append(df)
        collected_editions.append(edition)
        print(f"  {table_id} ({period_str}): {len(df)} rows")
        time.sleep(0.5)

    if not all_frames:
        return pd.DataFrame()

    if reconcile:
        edition_ids = [e["Identifier"] for e in collected_editions]
        merged, _log = reconcile_editions(all_frames, edition_ids, apply_concept_map=True)
    else:
        merged = pd.concat(all_frames, ignore_index=True, sort=False)

    # Move context columns to front
    context_cols = [c for c in ["_year", "_gm_code", "_geo_name", "_table_id"] if c in merged.columns]
    other_cols   = [c for c in merged.columns if c not in context_cols and c != "ID"]
    merged = merged[context_cols + other_cols]

    return merged


def _maybe_dedup_quarterly(df: pd.DataFrame) -> tuple[pd.DataFrame, str | None]:
    """
    For quarterly tables where all metric values are identical across periods,
    keep only the latest period. If values differ, return as-is with a note.
    """
    if "Perioden" not in df.columns:
        return df, None

    periods = df["Perioden"].unique()
    if len(periods) <= 1:
        return df, None

    skip = {"Perioden", "ID", "_gm_code", "_geo_name", "_year", "_table_id"}
    metric_cols = [c for c in df.columns
                   if c not in skip and pd.api.types.is_numeric_dtype(df[c])]
    group_keys  = [c for c in df.columns
                   if c not in skip and c not in metric_cols and c != "Perioden"]

    if not metric_cols or not group_keys:
        return df, None

    # Check: are numeric values constant within each group across periods?
    all_constant = (
        df.groupby(group_keys, sort=False)[metric_cols]
        .nunique()
        .le(1)
        .all()
        .all()
    )

    if not all_constant:
        return df, (
            f"Quarterly table with {len(periods)} periods — "
            "values differ across periods, all rows kept. "
            "Filter on Perioden before aggregating."
        )

    latest = df["Perioden"].max()
    deduped = df[df["Perioden"] == latest].copy()
    return deduped, (
        f"Quarterly table: values identical across all {len(periods)} periods, "
        f"auto-deduplicated to latest ({latest})."
    )


def compute_stats(df: pd.DataFrame) -> dict:
    """Return per-column summary stats for numeric columns."""
    stats = {}
    for col in df.columns:
        if col.startswith("_") or col == "ID":
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            col_data = df[col].dropna()
            null_count = int(df[col].isna().sum())
            stats[col] = {
                "count":      int(col_data.count()),
                "null_count": null_count,
                "null_pct":   round(null_count / len(df) * 100, 1) if len(df) > 0 else 0,
                "min":        float(col_data.min()) if len(col_data) else None,
                "max":        float(col_data.max()) if len(col_data) else None,
                "mean":       round(float(col_data.mean()), 2) if len(col_data) else None,
                "median":     round(float(col_data.median()), 2) if len(col_data) else None,
            }
    return stats


def _derive_year(df: pd.DataFrame) -> pd.Series:
    """Extract year from Perioden column if present."""
    if "Perioden" in df.columns:
        return df["Perioden"].str.extract(r"(\d{4})")[0].astype("Int64")
    return pd.Series([None] * len(df), dtype="Int64")


def _normalize_period(period: str) -> str:
    if re.fullmatch(r"\d{4}", period):
        return f"{period}JJ00"
    return period
