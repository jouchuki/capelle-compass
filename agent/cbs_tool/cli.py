"""
cbs — CBS StatLine CLI for agents.

Commands:
  search       Find tables by keyword (grouped by series by default)
  describe     Full human-readable schema — no raw CBS codes
  get          Fetch data (multi-city, multi-dim, full series)
  series       Show all annual editions of a table's series
  availability What data exists for a concept at a given geo level
  catalog      Build / update local catalog
"""
import json
import sys
from pathlib import Path
from typing import Annotated, Optional

import pandas as pd

import typer
from rich.console import Console
from rich.table import Table as RichTable

from .catalog import load_catalog, search as catalog_search, find_series, build_catalog
from .describe import describe_table
from .fetch import fetch_table, fetch_series, compute_stats
from .geo import parse_multi_geo, get_geo_name
from .concepts import availability as concept_availability

app     = typer.Typer(help="CBS StatLine data tool — designed for agent use.", no_args_is_help=True)
cat_app = typer.Typer(help="Catalog management.")
rag_app = typer.Typer(help="Semantic RAG index management.")
app.add_typer(cat_app, name="catalog")
app.add_typer(rag_app, name="rag")

console = Console()


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _out(data: dict):
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _clean_describe(result: dict) -> dict:
    """Remove internal _key fields from describe output."""
    clean = dict(result)
    clean_dims = {}
    for dim_name, vals in result.get("dimensions", {}).items():
        clean_dims[dim_name] = [
            {k: v for k, v in val.items() if k != "_key"}
            for val in vals
        ]
    clean["dimensions"] = clean_dims
    return clean


# ─── search ──────────────────────────────────────────────────────────────────

@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Search terms, e.g. 'werkloosheid buurt'")],
    geo:    Annotated[Optional[str], typer.Option(help="Filter by geo level: buurt, wijk, municipality")] = None,
    after:  Annotated[Optional[int], typer.Option(help="Only tables updated from this year onwards")] = None,
    limit:  Annotated[int, typer.Option(help="Max series results")] = 10,
    expand: Annotated[bool, typer.Option(help="Show individual editions instead of grouped series")] = False,
    fmt:    Annotated[str, typer.Option("--format")] = "json",
    no_rag: Annotated[bool, typer.Option("--no-rag", help="Force keyword search, skip RAG")] = False,
):
    """
    Search CBS tables by keyword or concept. Uses semantic RAG search if index is built,
    falls back to keyword search automatically. Build index with: cbs rag build
    """
    # Try RAG first (auto-detected, silent fallback)
    if not no_rag and not expand:
        try:
            from .rag.search import rag_search, is_available
            if is_available():
                result = rag_search(query, geo_level=geo, after=after, limit=limit)
                result["search_mode"] = "rag"
                _out(result)
                return
        except Exception:
            pass  # fall through to keyword

    tables  = load_catalog()
    results = catalog_search(query, tables, geo_level=geo, after=after,
                             limit=limit, group_series=not expand)

    if fmt == "md":
        _print_search_md(results, expand)
        return

    if expand:
        output = [
            {
                "id":         t.get("Identifier"),
                "title":      t.get("Title"),
                "period":     t.get("Period"),
                "geo_levels": t.get("geo_levels", []),
                "records":    t.get("RecordCount"),
            }
            for t in results
        ]
        _out({"query": query, "count": len(output), "results": output,
              "search_mode": "keyword",
              "next": [f"cbs describe {r['id']}" for r in output[:3]]})
    else:
        _out({
            "query": query,
            "count": len(results),
            "results": results,
            "search_mode": "keyword",
            "next": [f"cbs describe {s['latest_id']}" for s in results[:3] if s.get("latest_id")],
        })


# ─── describe ────────────────────────────────────────────────────────────────

@app.command()
def describe(
    table_id: Annotated[str, typer.Argument(help="CBS table ID, e.g. 86258NED")],
    fmt:      Annotated[str, typer.Option("--format")] = "json",
):
    """
    Full human-readable schema: dimensions with labels, metrics, geo levels, series.
    All dimension codes resolved to plain labels — no raw CBS codes in output.
    """
    result = describe_table(table_id)
    clean  = _clean_describe(result)

    if fmt == "md":
        _print_describe_md(clean)
    else:
        _out(clean)


# ─── get ─────────────────────────────────────────────────────────────────────

@app.command()
def get(
    table_id: Annotated[str, typer.Argument(help="CBS table ID")],
    geo:      Annotated[Optional[str], typer.Option(
                  help="Municipality name(s) or GM code(s), comma-separated. "
                       "e.g. 'Capelle aan den IJssel,Zoetermeer,Rotterdam'")] = None,
    level:    Annotated[str, typer.Option(
                  help="Geo level: municipality, wijk, buurt")] = "municipality",
    period:   Annotated[Optional[str], typer.Option(
                  help="Year or CBS period code, e.g. 2024 or 2024JJ00")] = None,
    dim:      Annotated[Optional[list[str]], typer.Option(
                  help="Dimension filter: 'DimName=Label', e.g. 'Geslacht=Mannen'. "
                       "Repeat for multiple filters.")] = None,
    all_series: Annotated[bool, typer.Option("--series",
                  help="Fetch all annual editions and merge into one dataset")] = False,
    stats:    Annotated[bool, typer.Option("--stats",
                  help="Return summary statistics instead of raw data")] = False,
    preview:  Annotated[bool, typer.Option("--preview",
                  help="Return first 5 rows only")] = False,
    fmt:      Annotated[str, typer.Option("--format",
                  help="Output format: json, csv, md")] = "json",
    out:      Annotated[Optional[Path], typer.Option("--out",
                  help="Save to file")] = None,
    output_json: Annotated[bool, typer.Option("--output-json",
                  help="Emit standardized ToolOutput JSON for dashboard")] = False,
):
    """
    Fetch CBS data. Supports multiple cities, dimension filters, full series fetch.

    Examples:
      cbs get 86258NED --geo 'Capelle aan den IJssel' --level buurt
      cbs get 86258NED --geo 'Capelle,Zoetermeer,Rotterdam' --level wijk
      cbs get 86258NED --geo 'Capelle' --dim 'Geslacht=Mannen' --dim 'Leeftijd=15 tot 25 jaar'
      cbs get 86003NED --geo 'Capelle,Zoetermeer' --level buurt --series --format csv --out ww.csv
    """
    try:
        gm_codes = parse_multi_geo(geo) if geo else []
    except ValueError as e:
        _out({"error": str(e), "next": ["cbs get " + table_id + " --geo 'GM0502' (use GM code directly)"]})
        raise typer.Exit(1)

    tables    = load_catalog()
    _meta     = next((t for t in tables if t.get("Identifier", "").lower() == table_id.lower()), {})
    frequency = _meta.get("Frequency", "")

    if all_series:
        editions = find_series(table_id, tables)
        if not editions:
            _out({"error": f"No series found for {table_id}", "next": [f"cbs describe {table_id}"]})
            raise typer.Exit(1)
        typer.echo(f"Fetching {len(editions)} editions...", err=True)
        df = fetch_series(editions, gm_codes, level=level, dim_filters=dim or [])
    else:
        df = fetch_table(table_id, gm_codes=gm_codes, level=level,
                         period=period, dim_filters=dim or [])

    if df.empty:
        _out({
            "rows":  0,
            "data":  [],
            "note":  "No data returned — check --geo, --level, or --period.",
            "next":  [f"cbs describe {table_id}",
                      f"cbs get {table_id} --geo 'Capelle aan den IJssel'"],
        })
        return

    if preview:
        df = df.head(5)

    typer.echo(f"  {len(df)} rows", err=True)

    if stats:
        stat_result = compute_stats(df)
        null_only = [col for col in df.columns
                     if not col.startswith("_") and col != "ID"
                     and pd.api.types.is_object_dtype(df[col]) and df[col].isna().all()]
        _out({
            "table":      table_id,
            "rows":       len(df),
            "data":       [],
            "stats":      stat_result,
            "null_only_columns": null_only or None,
            "next":       [f"cbs get {table_id} --geo '{geo}' --level {level} --format csv --out data.csv"],
        })
        return

    # Standardized output for dashboard
    if output_json:
        import math
        raw  = df.where(df.notna(), None).to_dict(orient="records")
        data = [
            {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in row.items()}
            for row in raw
        ]
        # Infer columns from DataFrame
        from pathlib import Path as _P
        sys.path.insert(0, str(_P(__file__).resolve().parent.parent))
        from capelle_rag.output_schema import ToolOutput, ColumnDef, ChartHint
        cols = []
        for c in df.columns:
            if c.startswith("_"):
                continue
            dtype = "number" if pd.api.types.is_numeric_dtype(df[c]) else "string"
            cols.append(ColumnDef(key=c, label=c, type=dtype))
        # Detect year column for chart hints
        year_col = "_year" if "_year" in df.columns else None
        numeric_cols = [c.key for c in cols if c.type == "number"][:3]
        hints = []
        if year_col and numeric_cols:
            hints.append(ChartHint(type="line", x="_year", y=numeric_cols, title=f"CBS {table_id}"))
        hints.append(ChartHint(type="table", x=cols[0].key if cols else "", y="", title=f"CBS {table_id}"))
        tool_out = ToolOutput(
            tool="cbs",
            query=f"cbs get {table_id} --geo {geo} --level {level}",
            result_type="table",
            data=data,
            columns=cols,
            metadata={"table_id": table_id, "geo": geo, "level": level, "rows": len(df)},
            chart_hints=hints,
        )
        typer.echo(tool_out.to_json())
        return

    # Output
    if fmt == "csv":
        content = df.to_csv(index=False)
        if out:
            out.write_text(content, encoding="utf-8")
            typer.echo(f"Saved → {out}", err=True)
        else:
            typer.echo(content)

    elif fmt == "md":
        content = df.to_markdown(index=False)
        if out:
            out.write_text(content or "", encoding="utf-8")
            typer.echo(f"Saved → {out}", err=True)
        else:
            typer.echo(content)

    else:
        import math
        raw  = df.where(df.notna(), None).to_dict(orient="records")
        data = [
            {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in row.items()}
            for row in raw
        ]
        null_counts = {
            col: int(df[col].isna().sum())
            for col in df.columns
            if not col.startswith("_") and df[col].isna().any()
        }
        freq_note    = df.attrs.get("dedup_note") or None
        rec_warnings = df.attrs.get("reconciliation_warnings") or None
        result = {
            "table":       table_id,
            "frequency":   frequency or None,
            "rows":        len(df),
            "geo":         geo,
            "level":       level,
            "null_counts": null_counts or None,
            "frequency_note":        freq_note,
            "reconciliation_warnings": rec_warnings,
            "data":        data,
            "next":        [
                f"cbs describe {table_id}",
                f"cbs series {table_id}",
                f"cbs get {table_id} --geo '{geo}' --level {level} --stats",
            ],
        }
        if out:
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str),
                           encoding="utf-8")
            typer.echo(f"Saved → {out}", err=True)
        else:
            _out(result)


# ─── series ──────────────────────────────────────────────────────────────────

@app.command()
def series(
    table_id: Annotated[str, typer.Argument(help="Any edition in the series")],
    diff:     Annotated[bool, typer.Option("--diff", help="Show schema changes (columns added/removed) between first and last edition")] = False,
    fmt:      Annotated[str, typer.Option("--format")] = "json",
):
    """
    Show all annual editions of the series this table belongs to.
    Use `cbs get <id> --series` to fetch across all editions.
    Use `--diff` to see which columns were added or removed over time.
    """
    tables  = load_catalog()
    matches = find_series(table_id, tables)

    if not matches:
        _out({"error": f"No series found for {table_id}",
              "next":  [f"cbs describe {table_id}"]})
        return

    editions = [
        {
            "id":         t["Identifier"],
            "period":     t.get("Period"),
            "geo_levels": t.get("geo_levels", []),
            "records":    t.get("RecordCount"),
        }
        for t in matches
    ]

    from .catalog import _normalize_title, _start_year_str
    base  = _normalize_title(matches[0].get("Title", ""))
    years = [_start_year_str(t.get("Period", "")) for t in matches]
    years = [y for y in years if y > 0]

    schema_note = None
    if len(matches) > 5:
        schema_note = (
            "Column names differ across editions (CBS ordinal suffix drift). "
            "The tool reconciles these automatically when using --series. "
            "Run --diff to see how many true concept changes remain after reconciliation."
        )

    result = {
        "series":        base,
        "edition_count": len(editions),
        "year_range":    f"{min(years)}–{max(years)}" if years else "",
        "editions":      editions,
        "schema_note":   schema_note,
        "next": [
            f"cbs get {table_id} --geo 'Capelle aan den IJssel' --level buurt --series",
            f"cbs get {table_id} --geo 'Capelle,Zoetermeer' --level buurt --series --format csv --out output.csv",
            f"cbs series {table_id} --diff",
        ],
    }

    if diff:
        result["schema_diff"] = _compute_schema_diff(matches)

    _out(result)


def _compute_schema_diff(editions: list[dict]) -> dict:
    """Compare DataProperties Topics between first and last edition."""
    import cbsodata
    from .catalog import catalog_url_for
    from .reconcile import strip_base

    if len(editions) < 2:
        return {"note": "Only one edition — no diff possible."}

    def get_topics(table_id: str) -> set[str]:
        try:
            dp = cbsodata.get_meta(table_id, "DataProperties", catalog_url=catalog_url_for(table_id))
            return {r.get("Key", "") for r in dp if r.get("Type") == "Topic" and r.get("Key")}
        except Exception:
            return set()

    first, last = editions[0], editions[-1]
    typer.echo(f"  Fetching schema for {first['Identifier']} and {last['Identifier']}...", err=True)
    first_cols = get_topics(first["Identifier"])
    last_cols  = get_topics(last["Identifier"])

    # Raw diff (CBS column names with ordinals — shows the full mismatch problem)
    added   = sorted(last_cols - first_cols)
    removed = sorted(first_cols - last_cols)

    # Post-reconciliation diff (ordinals stripped — shows true concept changes)
    first_bases = {strip_base(k) for k in first_cols}
    last_bases  = {strip_base(k) for k in last_cols}
    rec_added   = sorted(last_bases - first_bases)
    rec_removed = sorted(first_bases - last_bases)

    return {
        "from":                    {"id": first["Identifier"], "period": first.get("Period", "")},
        "to":                      {"id": last["Identifier"],  "period": last.get("Period", "")},
        # Raw (before reconciliation) — useful to see the full ordinal drift
        "raw_added":               added,
        "raw_removed":             removed,
        "raw_stable_count":        len(first_cols & last_cols),
        # After ordinal stripping — what --series actually produces now
        "reconciled_added":        rec_added,
        "reconciled_removed":      rec_removed,
        "reconciled_stable_count": len(first_bases & last_bases),
        "note": (
            f"Raw: {len(added)} added, {len(removed)} removed (mostly ordinal drift). "
            f"After reconciliation: {len(rec_added)} true concept additions, "
            f"{len(rec_removed)} true removals, {len(first_bases & last_bases)} stable columns."
        ),
    }


# ─── availability ─────────────────────────────────────────────────────────────

@app.command()
def availability(
    concept: Annotated[str, typer.Argument(
                 help="Concept to check, e.g. 'unemployment', 'income', 'housing'")],
    level:   Annotated[Optional[str], typer.Option(
                 help="Geo level: buurt, wijk, municipality, province, national")] = None,
    fmt:     Annotated[str, typer.Option("--format")] = "json",
):
    """
    What CBS data exists for a concept at a given geo level?
    Answers questions like: 'Is unemployment available at buurt level? Since when?'

    Known concepts: unemployment, employment, income, poverty, population,
                    housing, crime, education, benefits, youth care, energy
    """
    result = concept_availability(concept, level)
    _out(result)


# ─── municipalities ──────────────────────────────────────────────────────────

@app.command()
def municipalities(
    search: Annotated[Optional[str], typer.Argument(help="Filter by name substring")] = None,
    fmt:    Annotated[str, typer.Option("--format")] = "json",
):
    """
    List all Dutch municipalities with GM codes.
    Build the lookup first with: cbs catalog build

    Examples:
      cbs municipalities
      cbs municipalities capelle
      cbs municipalities rotterdam --format md
    """
    from .geo import _load_municipalities_file
    data = _load_municipalities_file()
    if not data:
        _out({
            "error": "Municipality database not built yet.",
            "next":  ["cbs catalog build"],
        })
        return

    by_code: dict[str, str] = data.get("by_code", {}) if "by_code" in data else data
    results = [
        {"code": code, "name": name}
        for code, name in sorted(by_code.items(), key=lambda x: x[1])
        if not search or search.lower() in name.lower()
    ]

    if fmt == "md":
        t = RichTable(title="Dutch Municipalities")
        t.add_column("GM code", style="cyan")
        t.add_column("Name")
        for r in results:
            t.add_row(r["code"], r["name"])
        console.print(t)
    else:
        _out({"count": len(results), "municipalities": results})


# ─── catalog ─────────────────────────────────────────────────────────────────

@cat_app.command("build")
def catalog_build(
    geo_enrich: Annotated[bool, typer.Option(
                    help="Accurate geo enrichment via live API calls (slow ~30min). "
                         "Default: fast heuristic from DefaultPresentation.")] = False,
):
    """
    Build local catalog from CBS API. Run once, then refresh with `cbs catalog update`.
    Fast mode (default) uses metadata heuristics — accurate mode calls API per table.
    """
    path = build_catalog(geo_enrich=geo_enrich, verbose=True)
    _out({"status": "ok", "catalog": str(path)})


@cat_app.command("update")
def catalog_update():
    """Re-build the catalog (same as build, overwrites existing)."""
    path = build_catalog(geo_enrich=False, verbose=True)
    _out({"status": "ok", "catalog": str(path)})


@cat_app.command("stats")
def catalog_stats():
    """Show stats about the current catalog."""
    tables = load_catalog()
    geo_counts: dict[str, int] = {}
    for t in tables:
        for level in t.get("geo_levels", []):
            geo_counts[level] = geo_counts.get(level, 0) + 1

    # Per-catalog breakdown
    catalog_counts: dict[str, int] = {}
    for t in tables:
        src = t.get("_catalog_url") or "opendata.cbs.nl"
        catalog_counts[src] = catalog_counts.get(src, 0) + 1

    from .catalog import _catalog_path
    _out({
        "catalog_path":  str(_catalog_path()),
        "total_tables":  len(tables),
        "by_catalog":    catalog_counts,
        "by_geo_level":  geo_counts,
        "has_buurt":     geo_counts.get("buurt", 0),
        "has_municipality": geo_counts.get("municipality", 0),
    })


# ─── md printers ─────────────────────────────────────────────────────────────

def _print_search_md(results, expand: bool):
    t = RichTable(title="CBS Search Results")
    if expand:
        t.add_column("ID", style="cyan")
        t.add_column("Title")
        t.add_column("Period")
        t.add_column("Geo levels")
        for r in results:
            t.add_row(r.get("id",""), (r.get("title") or "")[:60],
                      r.get("period",""), ", ".join(r.get("geo_levels",[])))
    else:
        t.add_column("Series", style="cyan")
        t.add_column("Editions")
        t.add_column("Years")
        t.add_column("Geo levels")
        t.add_column("Latest ID")
        for s in results:
            t.add_row(
                (s.get("series_title") or "")[:55],
                str(s.get("edition_count", 1)),
                s.get("year_range", ""),
                ", ".join(s.get("geo_levels", [])),
                s.get("latest_id", ""),
            )
    console.print(t)


def _print_describe_md(result: dict):
    console.print(f"\n[bold]{result.get('title')}[/bold]  [dim]{result.get('id')}[/dim]")
    console.print(f"Period: {result.get('period')}  |  Freq: {result.get('frequency')}  |  Records: {result.get('records'):,}" if result.get('records') else "")
    console.print(f"Geo levels: {', '.join(result.get('geo_levels', []))}\n")

    ser = result.get("series", {})
    if ser.get("edition_count", 1) > 1:
        console.print(f"[bold]Series:[/bold] {ser['base_title']}  ({ser['edition_count']} editions, {ser['year_range']})")
        console.print()

    console.print("[bold]Dimensions[/bold]")
    for dim, vals in result.get("dimensions", {}).items():
        console.print(f"  [cyan]{dim}[/cyan]:")
        for v in vals[:12]:
            if v.get("level"):
                console.print(f"    {v['level']:15s} {v.get('count','')} entries")
            else:
                marker = " [dim](total/default)[/dim]" if v.get("is_total") else ""
                console.print(f"    {v.get('label','')}{marker}")

    console.print("\n[bold]Metrics[/bold]")
    for m in result.get("metrics", []):
        if isinstance(m, dict):
            console.print(f"  [cyan]{m['column']}[/cyan]  {m['label']}")
        else:
            console.print(f"  {m}")

    notes = result.get("note", [])
    if notes:
        console.print("\n[bold yellow]Notes[/bold yellow]")
        for n in notes:
            console.print(f"  ⚠ {n}")

    console.print("\n[bold]Next steps[/bold]")
    for step in result.get("next", []):
        console.print(f"  [green]{step}[/green]")


# ─── rag ─────────────────────────────────────────────────────────────────────

@rag_app.command("enrich")
def rag_enrich(
    limit: Annotated[Optional[int], typer.Option(help="Limit to N tables (for testing)")] = None,
):
    """
    Fetch DataProperties (columns + dimensions) for all catalog tables.
    Saves to enriched_catalog.jsonl — resumable, skips already-done tables.
    Takes ~30 min for full catalog. Run once before `cbs rag index`.
    """
    from .rag.enrich import enrich
    path = enrich(limit=limit, verbose=True)
    _out({"status": "ok", "path": str(path)})


@rag_app.command("index")
def rag_index(
    reset: Annotated[bool, typer.Option(help="Wipe and rebuild index from scratch")] = False,
):
    """
    Build ChromaDB persistent local index from enriched catalog.
    Run after `cbs rag enrich`. Index is saved to cbs_tool/chroma_db/.
    """
    from .rag.index import build_index
    n = build_index(reset=reset, verbose=True)
    _out({"status": "ok", "new_series_indexed": n})


@rag_app.command("stats")
def rag_stats():
    """Show stats about the current RAG index."""
    from .rag.search import _get_collection, is_available, CHROMA_PATH
    from .rag.enrich import ENRICHED_PATH
    if not is_available():
        _out({"status": "not_built", "next": ["cbs rag enrich", "cbs rag index"]})
        return
    col = _get_collection()
    enriched_count = sum(1 for _ in open(ENRICHED_PATH)) if ENRICHED_PATH.exists() else 0
    _out({
        "status":          "ready",
        "series_indexed":  col.count(),
        "tables_enriched": enriched_count,
        "index_path":      str(CHROMA_PATH),
    })


if __name__ == "__main__":
    app()
