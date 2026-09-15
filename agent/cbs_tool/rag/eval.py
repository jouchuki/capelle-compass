"""
RAG evaluation for the self-reflective CBS table search pipeline.

Architecture under test
───────────────────────
  1. Embed query        OpenAI text-embedding-3-small
  2. ChromaDB top-20    cosine similarity over ~9000 CBS table documents
  3. Grade 0–3          gpt-4.1-nano judges each candidate:
                          0 = wrong topic
                          1 = related but wrong geo/period
                          2 = relevant, indirect match
                          3 = direct match
  4. Reformulate        if best grade < 3, the query is rewritten in Dutch CBS
                        terminology and the search retries (max 2 iterations)
  5. Return top-5       ranked by (grade, cosine score), with reasons

This loop is the key innovation: it catches cases where semantic similarity
is high but actual relevance is low (e.g. "train station" matches many
unrelated distance tables without grading), and improves precision by
reformulating into the exact statistical vocabulary CBS uses.

Metrics
───────
  recall@1   — expected table is the top result
  recall@5   — expected table appears anywhere in top 5
  mean_grade — average LLM grade of the expected table when found (max 3)
  mean_iters — average number of reformulation rounds triggered

Comparison mode runs the same 30 queries through plain keyword search so the
Delta column shows exactly how much the grading + reformulation loop adds.

Run
───
  python -m cbs_tool.rag.eval                        # full eval, auto-saves JSON
  python -m cbs_tool.rag.eval --no-compare           # skip keyword comparison
  python -m cbs_tool.rag.eval --category crime       # filter by category
  python -m cbs_tool.rag.eval --verbose              # show per-query grader reasoning
  python -m cbs_tool.rag.eval --out results.json     # explicit output path

"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Optional


# ─── Golden dataset ───────────────────────────────────────────────────────────
# Each entry: query → one or more accepted table IDs.
# Multiple IDs = any of them is a valid answer (e.g. different editions of same series).

GOLDEN: list[dict] = [
    # ── WW / Uitkering ──────────────────────────────────────────────────────
    {
        "query":        "werkloosheidsuitkering per buurt",
        "expected_ids": ["86003NED"],
        "geo":          "buurt",
        "category":     "benefits",
        "note":         "Core WW query, Dutch, buurt level",
    },
    {
        "query":        "bijstandsuitkering percentage inwoners wijken",
        "expected_ids": ["86003NED"],
        "geo":          "wijk",
        "category":     "benefits",
        "note":         "Welfare benefit, Dutch, wijk level",
    },
    {
        "query":        "WW uitkering soort uitkering",
        "expected_ids": ["86003NED"],
        "geo":          None,
        "category":     "benefits",
        "note":         "Explicit series name fragment",
    },
    {
        "query":        "unemployment benefit rate per neighbourhood",
        "expected_ids": ["86003NED"],
        "geo":          None,
        "category":     "benefits",
        "note":         "English query — tests cross-lingual embedding",
    },
    {
        "query":        "personen met uitkering AO AOW bijstand",
        "expected_ids": ["86003NED"],
        "geo":          None,
        "category":     "benefits",
        "note":         "Multi-benefit types query",
    },

    # ── Employment ──────────────────────────────────────────────────────────
    {
        "query":        "netto arbeidsparticipatie buurt",
        "expected_ids": ["86258NED"],
        "geo":          "buurt",
        "category":     "employment",
        "note":         "Buurt-level employment participation",
    },
    {
        "query":        "arbeidsdeelname per geslacht wijken buurten",
        "expected_ids": ["86258NED"],
        "geo":          "wijk",
        "category":     "employment",
        "note":         "Employment by gender, wijk/buurt series",
    },
    {
        "query":        "employment participation by gender neighbourhood",
        "expected_ids": ["86258NED"],
        "geo":          "buurt",
        "category":     "employment",
        "note":         "English query with geo filter",
    },
    {
        "query":        "werkloosheidspercentage gemeente",
        "expected_ids": ["86008NED"],
        "geo":          "municipality",
        "category":     "employment",
        "note":         "True unemployment rate — only available at municipality level",
    },
    {
        "query":        "arbeidsdeelname kerncijfers nationaal werkloosheidspercentage",
        "expected_ids": ["85264NED"],
        "geo":          None,
        "category":     "employment",
        "note":         "National unemployment rate",
    },

    # ── Income / Kerncijfers ────────────────────────────────────────────────
    {
        "query":        "gemiddeld inkomen per inwoner buurt",
        "expected_ids": ["86165NED"],
        "geo":          "buurt",
        "category":     "income",
        "note":         "Average income at buurt level",
    },
    {
        "query":        "inkomen ontvanger wijk kerncijfers",
        "expected_ids": ["86165NED"],
        "geo":          "wijk",
        "category":     "income",
        "note":         "Income per recipient, kerncijfers series",
    },
    {
        "query":        "average household income per neighbourhood",
        "expected_ids": ["86165NED"],
        "geo":          "buurt",
        "category":     "income",
        "note":         "English income query",
    },
    {
        "query":        "armoede laag inkomen personen buurt",
        "expected_ids": ["86165NED"],
        "geo":          "buurt",
        "category":     "poverty",
        "note":         "Poverty metric lives in Kerncijfers",
    },

    # ── Housing ─────────────────────────────────────────────────────────────
    {
        "query":        "WOZ waarde woningen gemiddeld buurt",
        "expected_ids": ["86165NED"],
        "geo":          "buurt",
        "category":     "housing",
        "note":         "WOZ value per buurt — in Kerncijfers",
    },
    {
        "query":        "koopwoningen huurwoningen woningcorporatie buurt",
        "expected_ids": ["86165NED"],
        "geo":          "buurt",
        "category":     "housing",
        "note":         "Housing tenure mix",
    },

    # ── Population / Demographics ────────────────────────────────────────────
    {
        "query":        "bevolking leeftijdsopbouw wijken buurten",
        "expected_ids": ["86165NED"],
        "geo":          "buurt",
        "category":     "population",
        "note":         "Age structure per neighbourhood",
    },
    {
        "query":        "aantal inwoners herkomst niet-westers buurt",
        "expected_ids": ["86165NED"],
        "geo":          "buurt",
        "category":     "population",
        "note":         "Population with migration background",
    },

    # ── Crime ────────────────────────────────────────────────────────────────
    {
        "query":        "geregistreerde misdrijven wijken buurten",
        "expected_ids": ["84468NED"],
        "geo":          "buurt",
        "category":     "crime",
        "note":         "Crime series — only 3 editions (2016–2018)",
    },
    {
        "query":        "criminaliteit veiligheid delicten per wijk",
        "expected_ids": ["84468NED"],
        "geo":          "wijk",
        "category":     "crime",
        "note":         "Crime by wijk using synonyms",
    },
    {
        "query":        "registered crimes by neighbourhood type",
        "expected_ids": ["84468NED"],
        "geo":          "buurt",
        "category":     "crime",
        "note":         "English crime query",
    },

    # ── Education ────────────────────────────────────────────────────────────
    {
        "query":        "opleidingsniveau bevolking wijk buurten",
        "expected_ids": ["86232NED", "86165NED"],
        "geo":          "wijk",
        "category":     "education",
        "note":         "Education level — dedicated series or Kerncijfers",
    },
    {
        "query":        "hbo wo diploma laagopgeleid buurt",
        "expected_ids": ["86165NED"],
        "geo":          "buurt",
        "category":     "education",
        "note":         "Education level in Kerncijfers",
    },

    # ── Energy ───────────────────────────────────────────────────────────────
    {
        "query":        "energieverbruik woningen gasverbruik buurt",
        "expected_ids": ["86159NED"],
        "geo":          "buurt",
        "category":     "energy",
        "note":         "Energy consumption per housing type",
    },
    {
        "query":        "elektriciteitsverbruik particuliere woningen woningtype",
        "expected_ids": ["86159NED"],
        "geo":          None,
        "category":     "energy",
        "note":         "Electricity consumption by housing type",
    },

    # ── Proximity / Nabijheid ────────────────────────────────────────────────
    {
        "query":        "afstand treinstation buurt",
        "expected_ids": ["86134NED"],
        "geo":          "buurt",
        "category":     "nabijheid",
        "note":         "Train station distance — from Final_Experiment",
    },
    {
        "query":        "nabijheid voorzieningen scholen huisarts",
        "expected_ids": ["86134NED"],
        "geo":          None,
        "category":     "nabijheid",
        "note":         "Proximity to services",
    },
    {
        "query":        "distance to facilities school station neighbourhood",
        "expected_ids": ["86134NED"],
        "geo":          None,
        "category":     "nabijheid",
        "note":         "English nabijheid query",
    },

    # ── Youth care ────────────────────────────────────────────────────────────
    {
        "query":        "jeugdzorg jongeren buurt",
        "expected_ids": ["86165NED"],
        "geo":          "buurt",
        "category":     "youth",
        "note":         "Youth care metric in Kerncijfers",
    },

    # ── Hard / ambiguous ──────────────────────────────────────────────────────
    {
        "query":        "structural unemployment exclusion neighbourhood",
        "expected_ids": ["86003NED", "86258NED"],
        "geo":          "buurt",
        "category":     "hard",
        "note":         "Vague English — either WW series or employment participation are valid",
    },
    {
        "query":        "sociaaleconomische status buurt inkomen werkloosheid",
        "expected_ids": ["86165NED", "86003NED"],
        "geo":          "buurt",
        "category":     "hard",
        "note":         "Multi-concept — socioeconomic status. Both Kerncijfers and uitkering are valid.",
    },
]


# ─── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class QueryResult:
    query:        str
    category:     str
    note:         str
    expected_ids: list[str]
    geo:          Optional[str]

    # RAG
    rag_hit_at1:  Optional[bool]  = None
    rag_hit_at5:  Optional[bool]  = None
    rag_grade:    Optional[int]   = None   # grade of expected result when found
    rag_iters:    int             = 0
    rag_top_id:   Optional[str]   = None
    rag_top_grade:Optional[int]   = None
    rag_error:    Optional[str]   = None

    # Keyword
    kw_hit_at1:   Optional[bool]  = None
    kw_hit_at5:   Optional[bool]  = None
    kw_error:     Optional[str]   = None


# ─── Evaluation runners ───────────────────────────────────────────────────────

def _run_rag(case: dict, verbose: bool) -> QueryResult:
    from .search import rag_search

    r = QueryResult(
        query        = case["query"],
        category     = case["category"],
        note         = case["note"],
        expected_ids = case["expected_ids"],
        geo          = case.get("geo"),
    )

    try:
        result = rag_search(
            case["query"],
            geo_level = case.get("geo"),
            limit     = 5,
        )
    except Exception as e:
        r.rag_error = str(e)
        return r

    results = result.get("results", [])
    r.rag_iters = len(result.get("iterations", []))

    if results:
        r.rag_top_id    = results[0]["id"]
        r.rag_top_grade = results[0].get("grade")

    ids_at5 = [res["id"] for res in results]
    ids_at1 = ids_at5[:1]

    r.rag_hit_at1 = any(eid in ids_at1 for eid in case["expected_ids"])
    r.rag_hit_at5 = any(eid in ids_at5 for eid in case["expected_ids"])

    # Grade of the expected result if found in top-5
    for res in results:
        if res["id"] in case["expected_ids"]:
            r.rag_grade = res.get("grade")
            break

    if verbose:
        print(f"\n  RAG results for: {case['query']!r}")
        for i, res in enumerate(results[:5]):
            marker = " <-- EXPECTED" if res["id"] in case["expected_ids"] else ""
            print(f"    [{i+1}] {res['id']}  grade={res.get('grade')}  score={res['score']:.3f}  {res['series'][:55]}{marker}")
        if r.rag_iters > 1:
            print(f"  Reformulated {r.rag_iters - 1} time(s)")

    return r


def _run_keyword(result: QueryResult) -> None:
    """Add keyword search results to an existing QueryResult in-place."""
    from ..catalog import load_catalog, search as catalog_search

    try:
        tables  = load_catalog()
        matches = catalog_search(result.query, tables, geo_level=result.geo, limit=5, group_series=True)
    except Exception as e:
        result.kw_error = str(e)
        return

    ids_at5 = [s.get("latest_id", "") for s in matches]
    ids_at1 = ids_at5[:1]

    result.kw_hit_at1 = any(eid in ids_at1 for eid in result.expected_ids)
    result.kw_hit_at5 = any(eid in ids_at5 for eid in result.expected_ids)


# ─── Metrics ─────────────────────────────────────────────────────────────────

def _summarise(results: list[QueryResult], mode: str) -> dict:
    total    = len(results)
    errors   = [r for r in results if (r.rag_error if mode == "rag" else r.kw_error)]
    valid    = [r for r in results if not (r.rag_error if mode == "rag" else r.kw_error)]

    if mode == "rag":
        hit1  = [r for r in valid if r.rag_hit_at1]
        hit5  = [r for r in valid if r.rag_hit_at5]
        grades = [r.rag_grade for r in valid if r.rag_grade is not None]
        iters  = [r.rag_iters for r in valid]
    else:
        hit1  = [r for r in valid if r.kw_hit_at1]
        hit5  = [r for r in valid if r.kw_hit_at5]
        grades = []
        iters  = []

    n = len(valid)
    return {
        "mode":         mode,
        "total":        total,
        "errors":       len(errors),
        "valid":        n,
        "recall_at_1":  round(len(hit1) / n, 3) if n else 0,
        "recall_at_5":  round(len(hit5) / n, 3) if n else 0,
        "mean_grade":   round(sum(grades) / len(grades), 2) if grades else None,
        "mean_iters":   round(sum(iters) / len(iters), 2) if iters else None,
    }


def _by_category(results: list[QueryResult], mode: str) -> dict:
    cats: dict[str, list] = {}
    for r in results:
        cats.setdefault(r.category, []).append(r)

    out = {}
    for cat, items in cats.items():
        s = _summarise(items, mode)
        out[cat] = {"recall@1": s["recall_at_1"], "recall@5": s["recall_at_5"], "n": s["valid"]}
    return out


# ─── Printing ─────────────────────────────────────────────────────────────────

def _print_table(results: list[QueryResult], compare: bool):
    """Print a formatted table of per-query results."""
    COL_W = 46
    header = (
        f"{'Query':<{COL_W}}  "
        f"{'Cat':<12}  "
        f"{'RAG@1':>5}  {'RAG@5':>5}  {'Grade':>5}  {'Iters':>5}"
    )
    if compare:
        header += f"  {'KW@1':>5}  {'KW@5':>5}"
    print("\n" + header)
    print("─" * len(header))

    for r in results:
        q_short = r.query[:COL_W]

        def fmt_bool(v):
            if v is None: return "  err"
            return "  HIT" if v else " miss"

        rag1  = fmt_bool(r.rag_hit_at1)
        rag5  = fmt_bool(r.rag_hit_at5)
        grade = f"  {r.rag_grade}" if r.rag_grade is not None else "    -"
        iters = f"  {r.rag_iters}" if r.rag_iters is not None else "    -"
        row = f"{q_short:<{COL_W}}  {r.category:<12}  {rag1}  {rag5}  {grade}  {iters}"

        if compare:
            kw1 = fmt_bool(r.kw_hit_at1)
            kw5 = fmt_bool(r.kw_hit_at5)
            row += f"  {kw1}  {kw5}"

        print(row)


def _print_summary(rag_summary: dict, kw_summary: Optional[dict]):
    print("\n" + "═" * 55)
    print("Summary")
    print("─" * 55)
    print(f"  Total queries:  {rag_summary['total']}")
    print(f"  Errors:         {rag_summary['errors']}")
    print()
    print(f"  {'Metric':<20}  {'RAG':>8}", end="")
    if kw_summary:
        print(f"  {'Keyword':>8}  {'Delta':>8}")
    else:
        print()
    print(f"  {'─'*20}  {'─'*8}", end="")
    if kw_summary:
        print(f"  {'─'*8}  {'─'*8}")
    else:
        print()

    metrics = [
        ("recall@1",  "recall_at_1"),
        ("recall@5",  "recall_at_5"),
        ("mean_grade","mean_grade"),
        ("mean_iters","mean_iters"),
    ]
    for label, key in metrics:
        rag_val = rag_summary.get(key)
        rag_str = f"{rag_val:.3f}" if isinstance(rag_val, float) else (str(rag_val) if rag_val is not None else "   -")
        row = f"  {label:<20}  {rag_str:>8}"
        if kw_summary:
            kw_val = kw_summary.get(key)
            kw_str = f"{kw_val:.3f}" if isinstance(kw_val, float) else (str(kw_val) if kw_val is not None else "   -")
            if isinstance(rag_val, float) and isinstance(kw_val, float):
                delta = rag_val - kw_val
                delta_str = f"{delta:+.3f}"
            else:
                delta_str = "   -"
            row += f"  {kw_str:>8}  {delta_str:>8}"
        print(row)

    print("═" * 55)


# ─── Entry point ─────────────────────────────────────────────────────────────

def run_eval(
    compare: bool = True,
    category: Optional[str] = None,
    verbose: bool = False,
    out: Optional[str] = None,
) -> dict:
    from .search import is_available

    if not is_available():
        print("ERROR: RAG index not built. Run: cbs rag enrich && cbs rag index", file=sys.stderr)
        sys.exit(1)

    cases = GOLDEN
    if category:
        cases = [c for c in cases if c["category"] == category]
        if not cases:
            print(f"ERROR: No golden cases for category '{category}'. "
                  f"Available: {sorted(set(c['category'] for c in GOLDEN))}", file=sys.stderr)
            sys.exit(1)

    print(f"Running RAG eval — {len(cases)} queries", end="")
    if category:
        print(f" (category: {category})", end="")
    print()

    results: list[QueryResult] = []
    for i, case in enumerate(cases):
        print(f"  [{i+1:2d}/{len(cases)}] {case['query'][:60]}", flush=True)
        r = _run_rag(case, verbose=verbose)
        if compare and not r.rag_error:
            _run_keyword(r)
        results.append(r)
        time.sleep(0.3)  # gentle rate limit on OpenAI

    _print_table(results, compare=compare)

    rag_summary = _summarise(results, "rag")
    kw_summary  = _summarise(results, "keyword") if compare else None
    _print_summary(rag_summary, kw_summary)

    rag_by_cat = _by_category(results, "rag")
    kw_by_cat  = _by_category(results, "keyword") if compare else None

    print("\nBy category (RAG recall@5):")
    for cat, stats in sorted(rag_by_cat.items(), key=lambda x: -x[1]["recall@5"]):
        kw_r5 = f"  kw={kw_by_cat[cat]['recall@5']:.2f}" if kw_by_cat else ""
        print(f"  {cat:<15}  rag={stats['recall@5']:.2f}{kw_r5}  (n={stats['n']})")

    full = {
        "rag":          rag_summary,
        "keyword":      kw_summary,
        "rag_by_cat":   rag_by_cat,
        "kw_by_cat":    kw_by_cat,
        "queries": [
            {
                "query":        r.query,
                "category":     r.category,
                "note":         r.note,
                "expected_ids": r.expected_ids,
                "geo":          r.geo,
                "rag_hit_at1":  r.rag_hit_at1,
                "rag_hit_at5":  r.rag_hit_at5,
                "rag_grade":    r.rag_grade,
                "rag_iters":    r.rag_iters,
                "rag_top_id":   r.rag_top_id,
                "kw_hit_at1":   r.kw_hit_at1,
                "kw_hit_at5":   r.kw_hit_at5,
                "rag_error":    r.rag_error,
            }
            for r in results
        ],
    }

    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(full, f, ensure_ascii=False, indent=2)
        print(f"\nResults saved → {out}")

    return full


def _apply_ollama_env(
    use_ollama_grader: bool,
    use_ollama_embed:  bool,
    grader_model:      str,
    embed_model:       str,
    ollama_url:        str,
) -> None:
    """Set env vars so search.py and embed.py pick up the local Ollama backend."""
    if use_ollama_grader:
        os.environ.setdefault("CBS_GRADER_BASE_URL", ollama_url)
        os.environ.setdefault("CBS_GRADER_MODEL",    grader_model)
        os.environ.setdefault("CBS_GRADER_API_KEY",  "ollama")
        print(f"  Grader:     {grader_model}  @ {ollama_url}")

    if use_ollama_embed:
        os.environ.setdefault("CBS_EMBED_BASE_URL", ollama_url)
        os.environ.setdefault("CBS_EMBED_MODEL",    embed_model)
        os.environ.setdefault("CBS_EMBED_API_KEY",  "ollama")
        print(f"  Embeddings: {embed_model}  @ {ollama_url}")
        print("  WARNING: index must have been built with the same embedding model.")
        print("           If you see poor results, rebuild: cbs rag index --reset")

    if not use_ollama_grader and not use_ollama_embed:
        return

    # Force-reset any cached clients so the new env vars take effect
    import cbs_tool.rag.embed as _embed_mod
    import cbs_tool.rag.search as _search_mod
    _embed_mod._client           = None
    _embed_mod._client_base_url  = None
    _search_mod._grader_client   = None
    _search_mod._grader_base_url = None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG evaluation against golden dataset")
    parser.add_argument("--no-compare",   action="store_true",
                        help="Skip keyword search comparison")
    parser.add_argument("--category",     default=None,
                        help="Only run queries in this category")
    parser.add_argument("--verbose",      action="store_true",
                        help="Show per-query RAG result details")
    parser.add_argument("--out",          default=None,
                        help="Save full results to JSON file (default: cbs_tool/rag/eval_results/eval_YYYYMMDD.json)")
    parser.add_argument("--ollama",       action="store_true",
                        help="Use local Ollama for the grader (embeddings still via OpenAI)")
    parser.add_argument("--ollama-embed", action="store_true",
                        help="Also use Ollama for embeddings (requires index rebuilt with same model)")
    parser.add_argument("--ollama-url",   default="http://localhost:11434/v1",
                        help="Ollama OpenAI-compatible base URL (default: http://localhost:11434/v1)")
    parser.add_argument("--grader-model", default="qwen2.5:14b",
                        help="Ollama model for grading (default: qwen2.5:14b)")
    parser.add_argument("--embed-model",  default="mxbai-embed-large",
                        help="Ollama model for embeddings (default: mxbai-embed-large)")
    args = parser.parse_args()

    use_embed = args.ollama_embed
    if use_embed and not args.ollama:
        # --ollama-embed implies --ollama
        args.ollama = True

    if args.ollama:
        print("Ollama mode:")
        _apply_ollama_env(
            use_ollama_grader = args.ollama,
            use_ollama_embed  = use_embed,
            grader_model      = args.grader_model,
            embed_model       = args.embed_model,
            ollama_url        = args.ollama_url,
        )

    # Default output path: eval_results/eval_YYYYMMDD.json
    out = args.out
    if out is None:
        from datetime import date
        results_dir = Path(__file__).parent / "eval_results"
        results_dir.mkdir(exist_ok=True)
        out = str(results_dir / f"eval_{date.today().strftime('%Y%m%d')}.json")

    run_eval(
        compare  = not args.no_compare,
        category = args.category,
        verbose  = args.verbose,
        out      = out,
    )
