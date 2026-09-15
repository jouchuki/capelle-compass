"""
capelle-beleid — Search Capelle policy documents (begrotingen, voorjaarsnota,
najaarsnota, jaarstukken, etc.) via semantic search.

All documents are indexed in the unified ChromaDB at capelle_rag/chroma_db/
under the 'policy_documents' collection, using OpenAI text-embedding-3-small.
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
from pathlib import Path

# Restore default SIGPIPE handling so `capelle-beleid search ... | head` exits
# cleanly instead of throwing a BrokenPipeError traceback when the downstream
# command closes the pipe. Without this, the ohrs agent sees a stderr
# traceback and abandons the beleid path, over-rotating onto CBS (which then
# blows up on --series dumps — see trajectory ebc430135515).
signal.signal(signal.SIGPIPE, signal.SIG_DFL)

REPO_ROOT = Path(__file__).resolve().parents[2]
BELEID_ROOT = Path(__file__).resolve().parents[1]
UNIFIED_DB = REPO_ROOT / "capelle_rag" / "chroma_db"
COLLECTION = "policy_documents"


def _get_collection():
    from .rag.client import get_chroma_client
    client = get_chroma_client()
    return client.get_collection(COLLECTION)


def cmd_search(args: argparse.Namespace) -> None:
    """Semantic search over policy documents."""
    sys.path.insert(0, str(REPO_ROOT))
    from cbs_tool.rag.embed import embed_one

    try:
        col = _get_collection()
    except Exception as e:
        print(json.dumps({"error": f"Collection not found: {e}. Run: capelle-beleid index"}))
        return

    emb = embed_one(args.query)

    # Build metadata filters
    where = {}
    conditions = []
    if args.doc_type:
        conditions.append({"doc_type": {"$eq": args.doc_type}})
    if args.year:
        conditions.append({"year": {"$eq": args.year}})

    if len(conditions) == 1:
        where = conditions[0]
    elif len(conditions) > 1:
        where = {"$and": conditions}

    kwargs = {
        "query_embeddings": [emb],
        "n_results": min(args.limit, col.count()),
        "include": ["documents", "metadatas", "distances"],
    }
    if where:
        kwargs["where"] = where

    results = col.query(**kwargs)

    items = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        score = round(1 - dist, 3)
        items.append({
            "text": doc[:500],
            "score": score,
            "doc_type": meta.get("doc_type"),
            "year": meta.get("year"),
            "document": meta.get("document"),
            "document_type": meta.get("document_type"),
            "program_number": meta.get("program_number"),
            "page_number": meta.get("page_number"),
            "source_url": meta.get("source_url", ""),
            "source_folder": meta.get("source_folder", ""),
        })

    output = {
        "query": args.query,
        "count": len(items),
        "filters": {},
        "results": items,
        "next": [],
    }
    if args.doc_type:
        output["filters"]["doc_type"] = args.doc_type
    if args.year:
        output["filters"]["year"] = args.year

    # Suggest follow-up searches
    if not args.doc_type and items:
        doc_types_seen = list(set(i["doc_type"] for i in items if i.get("doc_type")))[:3]
        for dt in doc_types_seen:
            output["next"].append(
                f"capelle-beleid search '{args.query}' --doc-type {dt}"
            )

    if args.output_json:
        sys.path.insert(0, str(REPO_ROOT))
        from capelle_rag.output_schema import ToolOutput, ColumnDef, ChartHint
        tool_out = ToolOutput(
            tool="beleid",
            query=args.query,
            result_type="search_results",
            data=items,
            columns=[
                ColumnDef(key="text", label="Fragment", type="text_snippet"),
                ColumnDef(key="score", label="Relevantie", type="number"),
                ColumnDef(key="doc_type", label="Document type", type="string"),
                ColumnDef(key="year", label="Jaar", type="year"),
                ColumnDef(key="document", label="Document", type="string"),
                ColumnDef(key="source_url", label="Bron", type="url"),
            ],
            metadata=output.get("filters", {}),
            chart_hints=[
                ChartHint(type="table", x="document", y="score", title=f"Beleidsdocumenten: {args.query}"),
            ],
        )
        print(tool_out.to_json())
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))


def cmd_index(args: argparse.Namespace) -> None:
    """Build or rebuild the policy_documents index."""
    subprocess.run(
        [sys.executable, str(BELEID_ROOT / "build_vectordb.py")]
        + (["--reset"] if args.reset else [])
        + (["--verbose"] if args.verbose else []),
        check=True,
        cwd=BELEID_ROOT,
    )


def cmd_config(args: argparse.Namespace) -> None:
    """Show index configuration and stats."""
    sys.path.insert(0, str(BELEID_ROOT))
    from config import PDF_ROOT, COLLECTION, discover_pdf_folders

    folders = discover_pdf_folders()
    total_pdfs = sum(len(list(f["path"].glob("*.pdf"))) for f in folders)

    info = {
        "pdf_root": str(PDF_ROOT),
        "pdf_root_exists": PDF_ROOT.exists(),
        "unified_db": str(UNIFIED_DB),
        "collection": COLLECTION,
        "folders": len(folders),
        "total_pdfs": total_pdfs,
        "doc_types": list(set(f["doc_type"] for f in folders)),
        "year_range": f"{min(f['year'] for f in folders)}–{max(f['year'] for f in folders)}" if folders else "n/a",
    }

    # Collection stats if available
    try:
        col = _get_collection()
        info["indexed_chunks"] = col.count()
    except Exception:
        info["indexed_chunks"] = 0

    print(json.dumps(info, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capelle-beleid",
        description="Search Capelle policy documents (begrotingen, voorjaarsnota, jaarstukken, etc.)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # search
    p_search = sub.add_parser("search", help="Semantic search over policy documents")
    p_search.add_argument("query", help="Search query (Dutch or English)")
    p_search.add_argument("--doc-type", help="Filter: begroting, voorjaarsnota, najaarsnota, jaarstukken, etc.")
    p_search.add_argument("--year", type=int, help="Filter by year")
    p_search.add_argument("--limit", type=int, default=8, help="Max results (default: 8)")
    p_search.add_argument("--output-json", action="store_true", help="Emit standardized ToolOutput JSON")
    p_search.set_defaults(func=cmd_search)

    # index
    p_index = sub.add_parser("index", help="Build/rebuild policy document index")
    p_index.add_argument("--reset", action="store_true", help="Drop and rebuild from scratch")
    p_index.add_argument("--verbose", "-v", action="store_true")
    p_index.set_defaults(func=cmd_index)

    # config
    p_config = sub.add_parser("config", help="Show configuration and index stats")
    p_config.set_defaults(func=cmd_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
