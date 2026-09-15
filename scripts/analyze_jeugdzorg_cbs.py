#!/usr/bin/env python3
"""Download and rank CBS jeugdzorg indicators for all municipalities.

The script deliberately uses only the standard library plus pandas from the
repo-agent venv. CBS OData tables reject unbounded large queries, so every
dataset request is paged under the 10k-row limit.
"""

from __future__ import annotations

import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import cbsodata
import pandas as pd


BASE = "https://opendata.cbs.nl/ODataApi/OData"
OUT = Path("data/analysis/jeugdzorg-cbs")
OUT.mkdir(parents=True, exist_ok=True)


TABLES = {
    "costs": "83454NED",
    "indicators": "85098NED",
    "core": "85099NED",
    "users": "85101NED",
    "protection": "82975NED",
    "reclassering": "82977NED",
}


def get_json(url: str, retries: int = 4) -> dict[str, Any]:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def endpoint(table: str, entity: str, **params: str | int) -> str:
    qs = urllib.parse.urlencode(params, safe="(), '$")
    return f"{BASE}/{table}/{entity}" + (f"?{qs}" if qs else "")


def service_entities(table: str) -> list[str]:
    payload = get_json(f"{BASE}/{table}")
    return [item["name"] for item in payload["value"]]


def fetch_entity(table: str, entity: str, params: dict[str, str | int] | None = None) -> pd.DataFrame:
    params = dict(params or {})
    if entity == "TypedDataSet" and (not params or set(params) == {"$filter"}):
        rows = cbsodata.get_data(table, filters=params.get("$filter"))
        return pd.DataFrame(rows)
    if "$top" in params and "$skip" not in params:
        payload = get_json(endpoint(table, entity, **params))
        return pd.DataFrame(payload.get("value", []))

    rows: list[dict[str, Any]] = []
    skip = 0
    top = 9999
    while True:
        page_params = dict(params)
        page_params["$top"] = top
        if skip:
            page_params["$skip"] = skip
        payload = get_json(endpoint(table, entity, **page_params))
        values = payload.get("value", [])
        rows.extend(values)
        if len(values) < top:
            break
        skip += top
    return pd.DataFrame(rows)


def fetch_table(table: str, params: dict[str, str | int] | None = None) -> pd.DataFrame:
    return fetch_entity(table, "TypedDataSet", params)


def meta_for(table: str) -> dict[str, Any]:
    entities = service_entities(table)
    meta: dict[str, Any] = {"entities": entities}
    for ent in entities:
        if ent in {"TypedDataSet", "UntypedDataSet"}:
            continue
        try:
            df = fetch_entity(table, ent)
        except Exception as exc:
            meta[ent] = {"error": str(exc)}
            continue
        meta[ent] = df.to_dict(orient="records")
    return meta


def write_meta() -> None:
    summary: dict[str, Any] = {}
    for name, table in TABLES.items():
        meta = meta_for(table)
        summary[name] = {
            "table": table,
            "entities": meta["entities"],
            "data_properties": meta.get("DataProperties", []),
            "periods": meta.get("Periods", []),
            "dimensions": {
                key: value[:20]
                for key, value in meta.items()
                if key not in {"entities", "DataProperties", "CategoryGroups", "Periods"}
                and isinstance(value, list)
            },
        }
        (OUT / f"{table}-meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (OUT / "meta-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2)[:12000])


def _metric_columns(meta: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in meta.get("DataProperties", []):
        if row.get("Type") != "Topic":
            continue
        key = row.get("Key")
        title = row.get("Title")
        if key and title:
            out[key] = title
    return out


def _label_map(meta: dict[str, Any], entity: str) -> dict[str, str]:
    rows = meta.get(entity, [])
    return {
        str(row.get("Key")): str(row.get("Title") or row.get("Description") or row.get("Key"))
        for row in rows
        if row.get("Key") is not None
    }


def _clean_code(value: Any) -> str:
    return str(value).strip()


def _region_label_map(meta: dict[str, Any]) -> tuple[str, dict[str, str]]:
    region_entity = next(
        (e for e in meta["entities"] if e.lower().startswith("regio")), "RegioS"
    )
    labels = {_clean_code(k): v for k, v in _label_map(meta, region_entity).items()}
    return region_entity, labels


def _municipality_label_set(meta: dict[str, Any]) -> set[str]:
    region_entity, _labels = _region_label_map(meta)
    return {
        str(row.get("Title") or "").strip()
        for row in meta.get(region_entity, [])
        if str(row.get("Key") or "").strip().startswith("GM")
    }


def _period_year(period: str) -> int | None:
    if period is None or (isinstance(period, float) and math.isnan(period)):
        return None
    period = str(period).strip()
    if len(period) < 4:
        return None
    import re
    match = re.search(r"\b(20\d{2}|19\d{2})\b", period)
    return int(match.group(1)) if match else None


def _is_annual_period_value(period: Any) -> bool:
    import re
    return bool(re.fullmatch(r"(19|20)\d{2}", str(period).strip()))


def _to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def load_meta(table: str) -> dict[str, Any]:
    path = OUT / f"{table}-meta.json"
    if not path.exists():
        path.write_text(json.dumps(meta_for(table), ensure_ascii=False, indent=2), encoding="utf-8")
    return json.loads(path.read_text(encoding="utf-8"))


def annual_cost_rankings() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    table = TABLES["costs"]
    meta = load_meta(table)
    df = fetch_table(table)
    df.to_csv(OUT / f"{table}-full.csv", index=False)

    _region_entity, region_labels = _region_label_map(meta)
    municipality_labels = _municipality_label_set(meta)
    zorg_entity = next((e for e in meta["entities"] if e.lower().startswith("zorg")), None)
    zorg_labels = _label_map(meta, zorg_entity) if zorg_entity else {}
    metrics = _metric_columns(meta)
    metric_cols = [c for c in df.columns if c in metrics]

    # Pick monetary metrics only. The table uses euro units in the DataProperties.
    money_cols = [
        c for c in metric_cols
        if "euro" in str(next((r for r in meta["DataProperties"] if r.get("Key") == c), {}).get("Unit", "")).lower()
        or "kosten" in metrics[c].lower()
    ]
    if not money_cols:
        money_cols = metric_cols

    dims = [c for c in df.columns if c not in {"ID", *metric_cols}]
    period_col = next(c for c in dims if c.lower().startswith(("period", "periode")))
    region_col = next((c for c in dims if c.lower().startswith(("regio", "region"))), None)
    if region_col is None:
        raise RuntimeError(f"No region column in {table}: {df.columns.tolist()}")
    zorg_col = next((c for c in dims if c != period_col and c != region_col), None)

    long = df.copy()
    long = long[long[period_col].map(_is_annual_period_value)]
    long["year"] = long[period_col].map(_period_year)
    long["region_code"] = long[region_col].map(_clean_code)
    long["gemeente"] = long["region_code"].map(region_labels).fillna(long["region_code"])
    if zorg_col:
        long["zorgvorm"] = long[zorg_col].map(zorg_labels).fillna(long[zorg_col])
    else:
        long["zorgvorm"] = "Totaal"
    for c in money_cols:
        long[c] = _to_num(long[c])

    # Keep municipalities. cbsodata returns labels for this table; raw API would
    # return GM codes. The cost table itself is municipality-only, but this keeps
    # the guard correct if the raw shape changes.
    if long["region_code"].astype(str).str.startswith("GM").any():
        long = long[long["region_code"].astype(str).str.startswith("GM")]
    elif municipality_labels:
        long = long[long["gemeente"].isin(municipality_labels)]

    total_mask = long["zorgvorm"].astype(str).str.contains("totaal", case=False, na=False)
    totals = long[total_mask].copy()
    if totals.empty:
        # Fallback: if no explicit total exists, aggregate all cost rows.
        totals = long.groupby(["region_code", "gemeente", "year"], as_index=False)[money_cols].sum()
        totals["zorgvorm"] = "Som zorgvormen"

    records: list[dict[str, Any]] = []
    cat_records: list[dict[str, Any]] = []
    for metric in money_cols:
        piv = totals.pivot_table(index=["region_code", "gemeente"], columns="year", values=metric, aggfunc="sum")
        years = sorted(y for y in piv.columns if isinstance(y, int))
        for y0, y1 in zip(years, years[1:]):
            tmp = piv[[y0, y1]].dropna().reset_index()
            tmp = tmp[(tmp[y0].abs() >= 100_000) | (tmp[y1].abs() >= 100_000)]
            for _, r in tmp.iterrows():
                old = float(r[y0])
                new = float(r[y1])
                records.append({
                    "metric": metrics.get(metric, metric),
                    "metric_key": metric,
                    "region_code": r["region_code"],
                    "gemeente": r["gemeente"],
                    "from_year": y0,
                    "to_year": y1,
                    "old": old,
                    "new": new,
                    "delta": new - old,
                    "pct": (new / old - 1) * 100 if old else math.nan,
                })
        for _, group in long.groupby(["region_code", "gemeente", "zorgvorm"]):
            if group["year"].nunique() < 2:
                continue
            cat = group.pivot_table(index=["region_code", "gemeente", "zorgvorm"], columns="year", values=metric, aggfunc="sum")
            years2 = sorted(y for y in cat.columns if isinstance(y, int))
            if len(years2) < 2:
                continue
            last0, last1 = years2[-2], years2[-1]
            rr = cat.reset_index().iloc[0]
            old = rr.get(last0)
            new = rr.get(last1)
            if pd.notna(old) and pd.notna(new) and (abs(float(old)) >= 50_000 or abs(float(new)) >= 50_000):
                cat_records.append({
                    "metric": metrics.get(metric, metric),
                    "metric_key": metric,
                    "region_code": rr["region_code"],
                    "gemeente": rr["gemeente"],
                    "zorgvorm": rr["zorgvorm"],
                    "from_year": last0,
                    "to_year": last1,
                    "old": float(old),
                    "new": float(new),
                    "delta": float(new) - float(old),
                    "pct": (float(new) / float(old) - 1) * 100 if float(old) else math.nan,
                })

    yoy = pd.DataFrame(records)
    cat_yoy = pd.DataFrame(cat_records)
    annual = totals[["region_code", "gemeente", "year", "zorgvorm", *money_cols]].copy()
    annual.to_csv(OUT / "costs_annual_totals.csv", index=False)
    yoy.to_csv(OUT / "costs_yoy_rankings.csv", index=False)
    cat_yoy.to_csv(OUT / "costs_category_latest_yoy.csv", index=False)
    return annual, yoy, cat_yoy


def fetch_current_table(table: str, filename: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    meta = load_meta(table)
    df = fetch_table(table)
    df.to_csv(OUT / filename, index=False)
    return df, meta


def describe_table_shape(table: str) -> dict[str, Any]:
    meta = load_meta(table)
    df = fetch_table(table, {"$top": 5})
    return {
        "table": table,
        "columns": df.columns.tolist(),
        "metrics": _metric_columns(meta),
        "entities": meta["entities"],
        "periods": meta.get("Periods", [])[-10:],
    }


def indicator_rankings() -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    shapes = {name: describe_table_shape(table) for name, table in TABLES.items() if name != "costs"}
    (OUT / "table-shapes.json").write_text(json.dumps(shapes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(shapes, ensure_ascii=False, indent=2)[:12000])

    # Current compact municipal indicator table: rates and counts by municipality.
    table = TABLES["indicators"]
    meta = load_meta(table)
    df = fetch_table(table)
    _region_entity, labels = _region_label_map(meta)
    municipality_labels = _municipality_label_set(meta)
    period_col = next(c for c in df.columns if c.lower().startswith(("period", "periode")))
    region_col = next(c for c in df.columns if c.lower().startswith(("regio", "region")))
    metrics = _metric_columns(meta)
    metric_cols = [c for c in df.columns if c in metrics]
    cur = df.copy()
    cur = cur[cur[period_col].map(_is_annual_period_value)]
    cur["year"] = cur[period_col].map(_period_year)
    cur["region_code"] = cur[region_col].map(_clean_code)
    cur["gemeente"] = cur["region_code"].map(labels).fillna(cur["region_code"])
    if cur["region_code"].astype(str).str.startswith("GM").any():
        cur = cur[cur["region_code"].astype(str).str.startswith("GM")]
    elif municipality_labels:
        cur = cur[cur["gemeente"].isin(municipality_labels)]
    for c in metric_cols:
        cur[c] = _to_num(cur[c])
    cur.to_csv(OUT / "indicator_current_full.csv", index=False)
    latest_year = int(cur["year"].max())
    prev_year = latest_year - 1
    latest = cur[cur["year"] == latest_year]
    prev = cur[cur["year"] == prev_year]
    wide = latest[["region_code", "gemeente", *metric_cols]].merge(
        prev[["region_code", *metric_cols]], on="region_code", how="left", suffixes=("", "_prev")
    )
    rows = []
    for c in metric_cols:
        for _, r in wide.iterrows():
            new = r.get(c)
            old = r.get(c + "_prev")
            if pd.notna(new):
                rows.append({
                    "metric_key": c,
                    "metric": metrics.get(c, c),
                    "region_code": r["region_code"],
                    "gemeente": r["gemeente"],
                    "year": latest_year,
                    "value": float(new),
                    "prev_year": prev_year,
                    "prev": float(old) if pd.notna(old) else math.nan,
                    "delta": float(new) - float(old) if pd.notna(old) else math.nan,
                    "pct": (float(new) / float(old) - 1) * 100 if pd.notna(old) and float(old) else math.nan,
                })
    rankings = pd.DataFrame(rows)
    rankings.to_csv(OUT / "indicator_rankings.csv", index=False)
    out["indicators"] = rankings

    out["careforms"] = careform_rankings()
    out["protection"] = trajectory_rankings(TABLES["protection"], "protection")
    out["reclassering"] = trajectory_rankings(TABLES["reclassering"], "reclassering")
    return out


def _latest_annual_periods(meta: dict[str, Any], n: int = 2) -> list[str]:
    periods = [
        row["Key"]
        for row in meta.get("Perioden", [])
        if str(row.get("Key", "")).endswith("JJ00")
    ]
    return periods[-n:]


def _or_filter(column: str, values: list[str]) -> str:
    return "(" + " or ".join(f"{column} eq '{value}'" for value in values) + ")"


def careform_rankings() -> pd.DataFrame:
    """Rank latest annual user/trajectory growth by care form for all gemeenten."""
    table = TABLES["core"]
    meta = load_meta(table)
    _region_entity, labels = _region_label_map(meta)
    municipality_labels = _municipality_label_set(meta)
    zorg_labels = _label_map(meta, "VormenVanJeugdzorg")
    selected = [
        "A045561",  # JZ Totaal jeugdzorg
        "A045560",  # JH 1 Totaal jeugdhulp
        "A042503",  # JH zonder verblijf
        "A017916",  # Uitgevoerd door wijkteam
        "A027917",  # Niet uitgevoerd door wijkteam
        "A027918",  # JH met verblijf
        "A027919",  # Jeugdbescherming
        "A027920",  # Jeugdreclassering
    ]
    periods = _latest_annual_periods(meta, 2)
    filt = " and ".join([
        _or_filter("VormenVanJeugdzorg", selected),
        _or_filter("Perioden", periods),
    ])
    df = fetch_table(table, {"$filter": filt})
    df.to_csv(OUT / "core_selected_careforms.csv", index=False)
    metric_cols = _metric_columns(meta)
    for c in metric_cols:
        if c in df.columns:
            df[c] = _to_num(df[c])
    region_col = "RegioS" if "RegioS" in df.columns else "Regio's"
    zorg_col = "VormenVanJeugdzorg" if "VormenVanJeugdzorg" in df.columns else "Vormen van jeugdzorg"
    period_col = "Perioden" if "Perioden" in df.columns else "Periods"
    df["region_code"] = df[region_col].map(_clean_code)
    if df["region_code"].str.startswith("GM").any():
        df = df[df["region_code"].str.startswith("GM")]
    df["gemeente"] = df["region_code"].map(labels).fillna(df["region_code"])
    if municipality_labels and not df["region_code"].str.startswith("GM").any():
        df = df[df["gemeente"].isin(municipality_labels)]
    df["zorgvorm"] = df[zorg_col].map(zorg_labels).fillna(df[zorg_col])
    df["year"] = df[period_col].map(_period_year)
    y0, y1 = [_period_year(p) for p in periods]
    rows: list[dict[str, Any]] = []
    for metric_key, metric_name in metric_cols.items():
        if metric_key not in df.columns:
            continue
        piv = df.pivot_table(
            index=["region_code", "gemeente", "zorgvorm"],
            columns="year",
            values=metric_key,
            aggfunc="sum",
        ).reset_index()
        if y0 not in piv.columns or y1 not in piv.columns:
            continue
        for _, r in piv.iterrows():
            old = r[y0]
            new = r[y1]
            if pd.isna(new):
                continue
            rows.append({
                "metric_key": metric_key,
                "metric": metric_name,
                "region_code": r["region_code"],
                "gemeente": r["gemeente"],
                "zorgvorm": r["zorgvorm"],
                "from_year": y0,
                "to_year": y1,
                "old": float(old) if pd.notna(old) else math.nan,
                "new": float(new),
                "delta": float(new) - float(old) if pd.notna(old) else math.nan,
                "pct": (float(new) / float(old) - 1) * 100 if pd.notna(old) and float(old) else math.nan,
            })
    result = pd.DataFrame(rows)
    result.to_csv(OUT / "core_careform_yoy_rankings.csv", index=False)
    return result


def trajectory_rankings(table: str, slug: str) -> pd.DataFrame:
    """Rank latest annual growth in started JB/JR trajectories by municipality."""
    meta = load_meta(table)
    _region_entity, labels = _region_label_map(meta)
    municipality_labels = _municipality_label_set(meta)
    periods = _latest_annual_periods(meta, 2)
    filt = _or_filter("Perioden", periods)
    df = fetch_table(table, {"$filter": filt})
    df.to_csv(OUT / f"{slug}_selected_annual.csv", index=False)
    metrics = _metric_columns(meta)
    metric_cols = [c for c in metrics if c in df.columns]
    for c in metric_cols:
        df[c] = _to_num(df[c])
    region_col = "RegioS" if "RegioS" in df.columns else "Regio's"
    period_col = "Perioden" if "Perioden" in df.columns else "Periods"
    df["region_code"] = df[region_col].map(_clean_code)
    if df["region_code"].str.startswith("GM").any():
        df = df[df["region_code"].str.startswith("GM")]
    df["gemeente"] = df["region_code"].map(labels).fillna(df["region_code"])
    if municipality_labels and not df["region_code"].str.startswith("GM").any():
        df = df[df["gemeente"].isin(municipality_labels)]
    df["year"] = df[period_col].map(_period_year)
    y0, y1 = [_period_year(p) for p in periods]
    rows = []
    for metric_key in metric_cols:
        piv = df.pivot_table(
            index=["region_code", "gemeente"],
            columns="year",
            values=metric_key,
            aggfunc="sum",
        ).reset_index()
        if y0 not in piv.columns or y1 not in piv.columns:
            continue
        for _, r in piv.iterrows():
            old = r[y0]
            new = r[y1]
            if pd.isna(new):
                continue
            rows.append({
                "source_table": table,
                "metric_key": metric_key,
                "metric": metrics[metric_key],
                "region_code": r["region_code"],
                "gemeente": r["gemeente"],
                "from_year": y0,
                "to_year": y1,
                "old": float(old) if pd.notna(old) else math.nan,
                "new": float(new),
                "delta": float(new) - float(old) if pd.notna(old) else math.nan,
                "pct": (float(new) / float(old) - 1) * 100 if pd.notna(old) and float(old) else math.nan,
            })
    result = pd.DataFrame(rows)
    result.to_csv(OUT / f"{slug}_trajectory_yoy_rankings.csv", index=False)
    return result


def print_top(title: str, df: pd.DataFrame, by: str, n: int = 15, cols: list[str] | None = None) -> None:
    print(f"\n## {title}")
    if df.empty:
        print("(empty)")
        return
    use = df.sort_values(by, ascending=False).head(n)
    if cols:
        use = use[cols]
    print(use.to_string(index=False))


def summarize() -> None:
    annual, yoy, cat_yoy = annual_cost_rankings()
    indicators = indicator_rankings()

    summary: dict[str, Any] = {
        "outputs": sorted(str(p) for p in OUT.glob("*")),
        "cost_years": sorted(int(y) for y in annual["year"].dropna().unique()),
    }
    (OUT / "run-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    if not yoy.empty:
        latest_to = int(yoy["to_year"].max())
        latest = yoy[yoy["to_year"] == latest_to].copy()
        print_top(
            f"Costs latest YoY absolute growth to {latest_to}",
            latest,
            "delta",
            cols=["gemeente", "from_year", "to_year", "metric", "old", "new", "delta", "pct"],
        )
        print_top(
            f"Costs latest YoY percent growth to {latest_to} (old >= 1m)",
            latest[latest["old"].abs() >= 1_000_000],
            "pct",
            cols=["gemeente", "from_year", "to_year", "metric", "old", "new", "delta", "pct"],
        )
        print_top(
            "Costs all-year absolute YoY spikes",
            yoy,
            "delta",
            cols=["gemeente", "from_year", "to_year", "metric", "old", "new", "delta", "pct"],
        )
    if not cat_yoy.empty:
        print_top(
            "Latest category cost YoY absolute growth",
            cat_yoy,
            "delta",
            cols=["gemeente", "zorgvorm", "from_year", "to_year", "old", "new", "delta", "pct"],
        )
    ind = indicators.get("indicators")
    if ind is not None and not ind.empty:
        print_top(
            "Latest indicator highest values",
            ind,
            "value",
            cols=["gemeente", "year", "metric", "value", "prev", "delta", "pct"],
        )
        print_top(
            "Latest indicator strongest YoY increase",
            ind[ind["prev"].notna()],
            "delta",
            cols=["gemeente", "year", "metric", "value", "prev", "delta", "pct"],
        )
    care = indicators.get("careforms")
    if care is not None and not care.empty:
        print_top(
            "Latest care-form user/trajectory YoY absolute growth",
            care,
            "delta",
            cols=["gemeente", "zorgvorm", "metric", "from_year", "to_year", "old", "new", "delta", "pct"],
        )
    for key in ("protection", "reclassering"):
        tr = indicators.get(key)
        if tr is not None and not tr.empty:
            started = tr[tr["metric_key"].isin(["TotaalBegonnenTrajecten_2", "NieuweTrajecten_3"])]
            print_top(
                f"Latest {key} started/new trajectory YoY growth",
                started,
                "delta",
                cols=["gemeente", "metric", "from_year", "to_year", "old", "new", "delta", "pct"],
            )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "meta":
        write_meta()
    else:
        summarize()
