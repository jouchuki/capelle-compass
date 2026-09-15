#!/usr/bin/env python3
"""
scripts/compare-minmax.py
-------------------------
Runs two queries against the `toge-inumaki` staging chroma and compares
three score representations per query:

    1. raw distance  (what chroma returns; cosine distance for policy_documents)
    2. per-query min-max  (normalized within the query's own top-k)
    3. pooled min-max     (normalized across BOTH queries' top-k concatenated)

The point: per-query min-max always lands in [0,1] regardless of how good
the matches actually were. It destroys cross-query comparability. Pooled
min-max preserves it. This script makes that visible with real numbers.

Usage:
    OPENAI_API_KEY=... python3 scripts/compare-minmax.py
    OPENAI_API_KEY=... python3 scripts/compare-minmax.py "query one" "query two"

The embedding model MUST match what the collection was built with —
`openai-text-embedding-3-small` (see the collection metadata on
toge-inumaki; dimension 1536). The default queries are deliberately
chosen so one is on-topic and one is off-topic, to make the information
loss from per-query normalization obvious.
"""

from __future__ import annotations

import os
import sys
from typing import Sequence

import chromadb
from openai import OpenAI

CHROMA_HOST = "toge-inumaki"
CHROMA_PORT = 8000
COLLECTION = "policy_documents"  # 14,939 rows, cosine space
EMBED_MODEL = "text-embedding-3-small"
TOP_K = 5

# Default queries — on-topic vs off-topic for Capelle policy docs.
DEFAULT_Q1 = "klimaatadaptatie en groene daken in de openbare ruimte"
DEFAULT_Q2 = "recept voor Hollandse bitterballen op de barbecue"


def embed(client: OpenAI, text: str) -> list[float]:
    resp = client.embeddings.create(model=EMBED_MODEL, input=text)
    return resp.data[0].embedding


def minmax(xs: Sequence[float]) -> list[float]:
    lo, hi = min(xs), max(xs)
    if hi == lo:
        return [0.0] * len(xs)
    return [(x - lo) / (hi - lo) for x in xs]


def main() -> None:
    q1 = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_Q1
    q2 = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_Q2

    openai = OpenAI()
    chroma = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    col = chroma.get_collection(name=COLLECTION)

    print(f"collection: {COLLECTION}  ({col.count()} rows, space=cosine)")
    print(f"embedding model: {EMBED_MODEL}")
    print(f"top_k: {TOP_K}")
    print(f"Q1: {q1!r}")
    print(f"Q2: {q2!r}\n")

    e1, e2 = embed(openai, q1), embed(openai, q2)
    r1 = col.query(query_embeddings=[e1], n_results=TOP_K,
                   include=["documents", "metadatas", "distances"])
    r2 = col.query(query_embeddings=[e2], n_results=TOP_K,
                   include=["documents", "metadatas", "distances"])

    d1, d2 = r1["distances"][0], r2["distances"][0]

    # Three normalizations
    mm1_self = minmax(d1)
    mm2_self = minmax(d2)
    pooled = minmax(list(d1) + list(d2))
    mm1_pool, mm2_pool = pooled[:TOP_K], pooled[TOP_K:]

    def dump(label: str, docs: list[str], metas: list[dict], raw: list[float],
             mm_self: list[float], mm_pool: list[float]) -> None:
        print(f"=== {label} ===")
        print(f"{'rank':>4}  {'raw':>8}  {'per-q mm':>9}  {'pooled mm':>10}   snippet")
        for i, (d, s, p, doc, meta) in enumerate(zip(raw, mm_self, mm_pool, docs, metas), start=1):
            snippet = (doc or "").replace("\n", " ")[:80]
            src = (meta or {}).get("source_url") or (meta or {}).get("file_path") or ""
            print(f"  {i:>2}  {d:8.4f}  {s:9.4f}  {p:10.4f}   {snippet}…")
            if src:
                print(f"       └─ {src}")
        print()

    dump("Q1", r1["documents"][0], r1["metadatas"][0], d1, mm1_self, mm1_pool)
    dump("Q2", r2["documents"][0], r2["metadatas"][0], d2, mm2_self, mm2_pool)

    # The punchline: a naive relevance threshold at mm >= 0.5 behaves
    # VERY differently depending on which normalization you chose.
    thr = 0.5
    print(f"=== what a mm >= {thr} threshold would return ===")
    print(f"  per-query mm:  Q1={sum(1 for x in mm1_self if x >= thr)} / {TOP_K}   "
          f"Q2={sum(1 for x in mm2_self if x >= thr)} / {TOP_K}")
    print(f"  pooled   mm:  Q1={sum(1 for x in mm1_pool if x >= thr)} / {TOP_K}   "
          f"Q2={sum(1 for x in mm2_pool if x >= thr)} / {TOP_K}")
    print()
    print("If Q1 has strong matches and Q2 has weak ones, per-query mm")
    print("flattens that difference to zero. Pooled mm preserves it.")


if __name__ == "__main__":
    main()
