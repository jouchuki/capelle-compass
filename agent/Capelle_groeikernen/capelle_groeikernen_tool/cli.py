"""
groeikernen — full-text search across the 7-groeikernen regelgeving corpus.

Subcommands
-----------
  search <query>     scan the corpus, return JSON with snippets + file paths
  coverage           how many docs of each kind are on disk
  show <doc_id>      print the file path(s) for one document id

The corpus lives at ``$GROEIKERNEN_DATA`` (default: the path inside
``compass/data/groeikernen``). It mixes CVDR cleaned Markdown and Gemeenteblad
raw HTML — both are searched, with HTML stripped on the fly.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from dataclasses import asdict
from pathlib import Path

from capelle_groeikernen_tool.corpus import (
    SOURCE_KINDS,
    Document, build_cache, data_root, discover, summarize_coverage,
    gemeente_slugs,
)
from capelle_groeikernen_tool.search import SearchHit, search

# Restore default SIGPIPE so `groeikernen search ... | head` exits cleanly
# instead of throwing a BrokenPipeError traceback at the user.
signal.signal(signal.SIGPIPE, signal.SIG_DFL)


def _hit_to_dict(hit: SearchHit) -> dict[str, object]:
    d = hit.document
    return {
        "gemeente": d.gemeente,
        "source": d.source,
        "doc_id": d.doc_id,
        "rubriek": d.rubriek,
        "cvdr_type": d.cvdr_type,
        "title": d.title,
        "date": d.date,
        "path": str(d.path),
        "score": hit.score,
        "matches": hit.matches_in_text,
        "snippet": hit.snippet,
    }


def cmd_search(args: argparse.Namespace) -> int:
    root = data_root()
    if not root.exists():
        print(json.dumps({
            "error": f"data root not found: {root}. "
                     f"Set $GROEIKERNEN_DATA or pass --data-root.",
        }), file=sys.stderr)
        return 2

    gemeenten = None
    if args.gemeente:
        known_slugs = gemeente_slugs(root)
        bad = [g for g in args.gemeente if g not in known_slugs]
        if bad:
            print(json.dumps({"error": f"unknown gemeente: {bad}. "
                                      f"Known: {sorted(known_slugs)}"}),
                  file=sys.stderr)
            return 2
        gemeenten = args.gemeente

    sources = args.source if args.source else None

    docs = discover(root=root, gemeenten=gemeenten, sources=sources)
    if args.rubriek:
        wanted = {r.lower() for r in args.rubriek}
        docs = [d for d in docs
                if (d.rubriek and d.rubriek.lower() in wanted)
                or (d.cvdr_type and d.cvdr_type.lower() in wanted)]

    result_page = search(
        docs, args.query,
        page=args.page, page_size=args.page_size,
        context_words=args.context_words,
        sort=args.sort,
    )

    output: dict[str, object] = {
        "query": args.query,
        "total": result_page.total,
        "page": result_page.page,
        "page_size": result_page.page_size,
        "returned": len(result_page.hits),
        "has_more": result_page.has_more,
        "sort": args.sort,
        "filters": {
            "gemeente": args.gemeente or None,
            "source": args.source or None,
            "rubriek": args.rubriek or None,
        },
        "context_words": args.context_words,
        "results": [_hit_to_dict(h) for h in result_page.hits],
        "next": [],
    }

    # 1) "Next page" hint when there's more.
    if result_page.has_more:
        flag_str = _rebuild_flags(args)
        cmd_parts = ["groeikernen search", json.dumps(args.query)]
        if flag_str:
            cmd_parts.append(flag_str)
        cmd_parts.append(f"--page {result_page.page + 1}")
        output["next"].append(" ".join(cmd_parts))
    # 2) Narrow by gemeente if we span several and the user didn't pin one.
    if not args.gemeente and result_page.hits:
        seen = sorted({h.document.gemeente for h in result_page.hits})
        for slug in seen[:3]:
            output["next"].append(
                f"groeikernen search {json.dumps(args.query)} --gemeente {slug}"
            )
    # 3) Read the top hit directly.
    if result_page.hits:
        top = result_page.hits[0].document
        output["next"].append(f"groeikernen show {top.doc_id}")

    print(json.dumps(output, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _rebuild_flags(args: argparse.Namespace) -> str:
    """Reconstruct the user's filter flags for a next-page hint."""
    parts: list[str] = []
    for g in (args.gemeente or []):
        parts.append(f"--gemeente {g}")
    for s in (args.source or []):
        parts.append(f"--source {s}")
    for r in (args.rubriek or []):
        parts.append(f"--rubriek {json.dumps(r)}")
    if args.page_size != 50:
        parts.append(f"--page-size {args.page_size}")
    if args.context_words != 100:
        parts.append(f"--context-words {args.context_words}")
    if args.sort != "score":
        parts.append(f"--sort {args.sort}")
    return " ".join(parts)


def cmd_coverage(args: argparse.Namespace) -> int:
    cov = summarize_coverage()
    output = {
        "data_root": str(data_root()),
        "totals": cov.totals,
        "by_gemeente": cov.by_gemeente,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    docs = discover()
    print(f"indexing {len(docs)} documents into text cache...",
          file=sys.stderr, flush=True)
    built, skipped = build_cache(docs, force=args.force)
    print(json.dumps({
        "indexed": built + skipped,
        "rebuilt": built,
        "skipped": skipped,
    }))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    target = args.doc_id
    docs = discover()
    matches = [d for d in docs if d.doc_id == target]
    if not matches:
        print(json.dumps({"error": f"no document with id={target}"}),
              file=sys.stderr)
        return 2
    output = {
        "doc_id": target,
        "count": len(matches),
        "results": [
            {
                "gemeente": d.gemeente,
                "source": d.source,
                "rubriek": d.rubriek,
                "cvdr_type": d.cvdr_type,
                "title": d.title,
                "date": d.date,
                "path": str(d.path),
            }
            for d in matches
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="groeikernen",
        description="Full-text search across the 7-groeikernen corpus "
                    "(CVDR verordeningen + Gemeenteblad regelgeving).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_search = sub.add_parser("search", help="search the corpus")
    p_search.add_argument("query", help="search term (case-insensitive)")
    p_search.add_argument(
        "--gemeente", action="append",
        help="Filter to one or more gemeente slugs. Repeatable.",
    )
    p_search.add_argument(
        "--source", action="append", choices=list(SOURCE_KINDS),
        help="Filter to one or more sources. Repeatable.",
    )
    p_search.add_argument(
        "--rubriek", action="append",
        help="Filter to specific rubrieken / CVDR types "
             "(e.g. 'beleidsregel', 'verordening'). Repeatable.",
    )
    p_search.add_argument(
        "--page", type=int, default=1,
        help="1-indexed page to return (default 1).",
    )
    p_search.add_argument(
        "--page-size", type=int, default=50,
        help="Results per page (default 50).",
    )
    p_search.add_argument(
        "--sort", choices=["score", "gemeente", "date"], default="score",
        help="Result ordering. 'score' (default) = hit count desc. "
             "'gemeente' = alphabetical by municipality slug (groups results "
             "per gemeente). 'date' = newest publication first.",
    )
    p_search.add_argument(
        "--context-words", type=int, default=100,
        help="Approx. words of context to include in each snippet.",
    )
    p_search.add_argument("--pretty", action="store_true",
                          help="Pretty-print JSON output.")
    p_search.set_defaults(func=cmd_search)

    p_cov = sub.add_parser("coverage", help="corpus size per gemeente/source")
    p_cov.add_argument("--pretty", action="store_true",
                       help="Pretty-print JSON output.")
    p_cov.set_defaults(func=cmd_coverage)

    p_idx = sub.add_parser("index",
                           help="pre-build the text cache (one-time, ~30s)")
    p_idx.add_argument("--force", action="store_true",
                       help="Rebuild even entries that look up-to-date.")
    p_idx.set_defaults(func=cmd_index)

    p_show = sub.add_parser("show", help="locate file path(s) for a doc id")
    p_show.add_argument("doc_id",
                        help="e.g. 'CVDR337993_v1' or 'gmb-2026-224348'")
    p_show.add_argument("--pretty", action="store_true",
                        help="Pretty-print JSON output.")
    p_show.set_defaults(func=cmd_show)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
