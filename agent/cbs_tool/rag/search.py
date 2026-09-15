"""
Self-reflective RAG search over CBS table metadata.

Flow:
  1. Embed query        — embedding model (default: OpenAI text-embedding-3-small)
  2. ChromaDB top-20    — cosine similarity, optional metadata pre-filter
  3. Grade candidates   — chat model (default: gpt-4.1-nano-2025-04-14), score 0–3
  4. If best score < 3  — reformulate query, retry (max 2 iterations)
  5. Return top-5 with relevance explanations

Grader can be swapped to local Ollama without rebuilding the index:
  CBS_GRADER_BASE_URL = http://localhost:11434/v1
  CBS_GRADER_MODEL    = qwen2.5:14b
  CBS_GRADER_API_KEY  = ollama

Embeddings can also be swapped, but then the index must be rebuilt:
  CBS_EMBED_BASE_URL / CBS_EMBED_MODEL / CBS_EMBED_API_KEY  (see embed.py)
"""
from __future__ import annotations

from typing import Optional

import chromadb

from .client import get_chroma_client
from .embed import embed_one

COLLECTION  = "cbs_tables"

_collection: chromadb.Collection | None = None


def _get_collection() -> chromadb.Collection:
    global _collection
    if _collection is None:
        client      = get_chroma_client()
        _collection = client.get_collection(COLLECTION)
    return _collection


def is_available() -> bool:
    """Return True if index exists and can be queried."""
    try:
        col = _get_collection()
        return col.count() > 0
    except Exception:
        return False


# ─── Core retrieval ───────────────────────────────────────────────────────────

def _retrieve(
    query: str,
    n: int = 20,
    geo_level: Optional[str] = None,
    after: Optional[int] = None,
) -> list[dict]:
    col = _get_collection()
    emb = embed_one(query)

    # Map geo_level to the boolean metadata field stored per series
    GEO_FILTER = {
        "buurt":        {"has_buurt":        {"$eq": True}},
        "wijk":         {"has_buurt":        {"$eq": True}},  # wijk tables always include buurt
        "municipality": {"has_municipality": {"$eq": True}},
    }
    where: dict = {}
    geo_filter = GEO_FILTER.get(geo_level) if geo_level else None

    if geo_filter and after:
        geo_key, geo_val = list(geo_filter.items())[0]
        where = {"$and": [geo_filter, {"year_max": {"$gte": after}}]}
    elif geo_filter:
        where = geo_filter
    elif after:
        where = {"year_max": {"$gte": after}}

    kwargs: dict = {
        "query_embeddings": [emb],
        "n_results":        min(n, col.count()),
        "include":          ["documents", "metadatas", "distances"],
    }
    if where:
        kwargs["where"] = where

    results = col.query(**kwargs)

    # Deduplicate chunks: keep best-scoring chunk per series (latest_id)
    best: dict[str, dict] = {}
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        lid   = meta.get("latest_id")
        score = round(1 - dist, 3)
        if lid not in best or score > best[lid]["score"]:
            best[lid] = {
                "id":       lid,
                "series":   meta.get("series_base"),
                "geo":      meta.get("geo_levels", "").split(","),
                "years":    f"{meta.get('year_min')}–{meta.get('year_max')}",
                "freq":     meta.get("frequency"),
                "score":    score,
                "document": doc,
                "meta":     meta,
            }
    return list(best.values())


# ─── Grader client (separate from embedding client) ───────────────────────────

_DEFAULT_GRADER_MODEL = "gpt-4.1-nano-2025-04-14"

_grader_client = None
_grader_base_url: str | None = None


def _get_grader_config() -> tuple[str, str, str | None]:
    """Return (api_key, model, base_url) for the grader. Reads env vars fresh."""
    import os
    base_url = os.environ.get("CBS_GRADER_BASE_URL")
    model    = os.environ.get("CBS_GRADER_MODEL", _DEFAULT_GRADER_MODEL)
    api_key  = os.environ.get("CBS_GRADER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "No API key set for grader. "
            "Set OPENAI_API_KEY for OpenAI, or CBS_GRADER_API_KEY for local Ollama."
        )
    return api_key, model, base_url


def _get_grader_client():
    global _grader_client, _grader_base_url
    from openai import OpenAI
    api_key, _, base_url = _get_grader_config()
    if _grader_client is None or _grader_base_url != base_url:
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        _grader_client   = OpenAI(**kwargs)
        _grader_base_url = base_url
    return _grader_client


# kept for backwards compatibility
def _get_openai_client():
    return _get_grader_client()


def _grade_results(query: str, candidates: list[dict]) -> list[dict]:
    """
    Grade each candidate 0–3 for relevance to query using gpt-4.1-nano-2025-04-14.
    Falls back to ungraded (semantic score only) if unavailable.
    """
    try:
        import json, re
        client = _get_openai_client()
    except Exception as e:
        for c in candidates:
            c["grade"]  = None
            c["reason"] = f"grading unavailable: {e}"
        return candidates

    items = "\n\n".join(
        f"[{i+1}] {c['document'][:600]}"
        for i, c in enumerate(candidates)
    )

    prompt = f"""You are grading CBS (Statistics Netherlands) table search results.

Query: "{query}"

For each table below, give:
- score: 0 (wrong topic), 1 (related but wrong geo/period), 2 (relevant, indirect), 3 (direct match)
- reason: one short sentence

{items}

Respond as JSON array: [{{"index": 1, "score": 2, "reason": "..."}}, ...]"""

    try:
        _, grader_model, _ = _get_grader_config()
        response = client.chat.completions.create(
            model      = grader_model,
            max_tokens = 1024,
            messages   = [{"role": "user", "content": prompt}],
        )
        raw   = response.choices[0].message.content
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            grades    = json.loads(match.group())
            grade_map = {g["index"]: g for g in grades}
            for i, c in enumerate(candidates):
                g = grade_map.get(i + 1, {})
                c["grade"]  = g.get("score")
                c["reason"] = g.get("reason", "")
    except Exception as e:
        for c in candidates:
            c["grade"]  = None
            c["reason"] = f"grading error: {e}"

    return candidates


def _reformulate(query: str, best_candidate: dict) -> str:
    """Rephrase the query in Dutch CBS terminology based on why the best match failed."""
    try:
        client = _get_grader_client()
        _, grader_model, _ = _get_grader_config()
        response = client.chat.completions.create(
            model      = grader_model,
            max_tokens = 64,
            messages   = [{
                "role":    "user",
                "content": (
                    f'Original query: "{query}"\n'
                    f'Best CBS table found: "{best_candidate["series"]}"\n'
                    f'Why it failed: "{best_candidate.get("reason", "")}"\n\n'
                    "Suggest a better search query in Dutch that would find the right CBS table. "
                    "Use Dutch statistical terminology. Output only the query string, nothing else."
                ),
            }],
        )
        return response.choices[0].message.content.strip().strip('"')
    except Exception:
        return query


# ─── Main search entry point ──────────────────────────────────────────────────

def rag_search(
    query: str,
    geo_level: Optional[str] = None,
    after: Optional[int] = None,
    limit: int = 5,
    max_iterations: int = 2,
) -> dict:
    """
    Self-reflective RAG search. Returns ranked results with grades and reasons.
    """
    current_query = query
    all_iterations = []
    graded: list = []

    for iteration in range(max_iterations):
        candidates = _retrieve(current_query, n=20, geo_level=geo_level, after=after)
        if not candidates:
            break

        graded = _grade_results(query, candidates)  # always grade against original query
        graded.sort(key=lambda c: (c.get("grade") or 0, c["score"]), reverse=True)

        all_iterations.append({
            "query":      current_query,
            "best_grade": graded[0].get("grade"),
        })

        best_grade = graded[0].get("grade")

        # If grading unavailable or direct match found — stop. Grade 2 = indirect, still try to improve.
        if best_grade is None or best_grade >= 3:
            break

        # Reformulate and retry
        if iteration < max_iterations - 1:
            new_query = _reformulate(query, graded[0])
            if new_query == current_query:
                break
            current_query = new_query
        else:
            break

    top = graded[:limit]

    return {
        "query":       query,
        "iterations":  all_iterations,
        "count":       len(top),
        "results": [
            {
                "id":     r["id"],
                "series": r["series"],
                "geo":    r["geo"],
                "years":  r["years"],
                "freq":   r["freq"],
                "score":  r["score"],
                "grade":  r.get("grade"),
                "reason": r.get("reason"),
                "next": [
                    f"cbs describe {r['id']}",
                    f"cbs get {r['id']} --geo '<city>' --level {'buurt' if 'buurt' in r['geo'] else 'municipality'}",
                ],
            }
            for r in top
        ],
    }
