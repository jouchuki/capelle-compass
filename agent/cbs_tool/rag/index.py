"""
Build / update the ChromaDB persistent local index from enriched catalog.
Run: python -m cbs_tool.rag.index [--reset]
"""
import sys
from pathlib import Path

import chromadb

from .client import get_chroma_client
from .enrich import load_enriched
from .build_docs import build_series_chunks, group_by_series
from .embed import embed_batch

from .paths import CHROMA_PATH, CBS_DATA_DIR
COLLECTION  = "cbs_tables"


def get_collection(reset: bool = False) -> chromadb.Collection:
    CBS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    client = get_chroma_client()
    if reset:
        try:
            client.delete_collection(COLLECTION)
        except Exception:
            pass
    return client.get_or_create_collection(
        COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


def build_index(reset: bool = False, verbose: bool = True) -> int:
    if verbose:
        print(f"Loading enriched catalog...", flush=True)
    tables = load_enriched()

    if verbose:
        print(f"  {len(tables)} tables loaded", flush=True)

    collection = get_collection(reset=reset)

    existing = set(collection.get(include=[])["ids"])
    if verbose and existing:
        print(f"  {len(existing)} series already indexed", flush=True)

    series_groups = group_by_series(tables)
    if verbose:
        print(f"  {len(series_groups)} unique series", flush=True)

    ids, docs, metas = [], [], []
    skipped = 0
    from ..catalog import _start_year_str

    for i, (series_base, editions) in enumerate(series_groups.items()):
        latest_id = sorted(
            editions,
            key=lambda t: _start_year_str(t.get("Period", ""))
        )[-1]["Identifier"]

        if latest_id in existing and not reset:
            skipped += 1
            continue

        try:
            chunks = build_series_chunks(editions)
        except Exception as e:
            if verbose:
                print(f"  Skipping {series_base[:50]}: {e}", flush=True)
            continue

        for doc, meta in chunks:
            chunk_id = meta.get("chunk_id", meta["latest_id"])
            ids.append(chunk_id)
            docs.append(doc)
            metas.append(meta)

        if verbose and i % 100 == 0:
            print(f"  Building documents {i}/{len(series_groups)}...", flush=True)

    if not ids:
        if verbose:
            print(f"Nothing to index ({skipped} series already up to date)")
        return 0

    if verbose:
        print(f"Embedding {len(ids)} chunks for {len(ids) - skipped} series via OpenAI...", flush=True)

    all_embeddings = embed_batch(docs, verbose=verbose)

    if verbose:
        print("Upserting to ChromaDB...", flush=True)

    # Upsert in batches (ChromaDB has limits)
    for i in range(0, len(ids), 100):
        collection.upsert(
            ids        = ids[i:i+100],
            documents  = docs[i:i+100],
            embeddings = all_embeddings[i:i+100],
            metadatas  = metas[i:i+100],
        )

    total = len(existing) + len(ids)
    if verbose:
        print(f"Done — {total} chunks in index ({len(ids)} new, {skipped} series skipped) → {CHROMA_PATH}")
    return len(ids)


if __name__ == "__main__":
    reset = "--reset" in sys.argv
    build_index(reset=reset)
