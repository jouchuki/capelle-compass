"""
Search engine: case-insensitive substring match with a context window of
~100 words centred on the strongest match.

Speed strategy
--------------
The corpus is ~14k pre-stripped text files (~250 MB total). For broad
queries Python regex over all of them takes ~8 s — too slow for an agent
CLI. So we shell out to **ripgrep** (``rg``) as the candidate filter when
it's available: ``rg -c -i`` returns ``path:match_count`` per matching
file in 10–50 ms across the whole corpus. Python work is then reduced
to the top-N matching files only: re-read each one, find the strongest
match offset, extract a snippet.

Falls back to pure-Python regex scan if ``rg`` isn't on $PATH, so the
tool still works on stripped-down containers.

Scoring
-------
- Score = full-query hit count (rg ``-c``) when using ripgrep.
- Score = 3 × full-query hits + (number of distinct query tokens that
  matched) when using the Python fallback. Marginally more nuanced but
  not worth losing the 400× rg speedup in the common case.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

from capelle_groeikernen_tool.corpus import (
    Document,
    _cache_path_for,
    _cache_root,
    load_text,
)


@dataclass
class SearchHit:
    """One scored search result with snippet + path."""

    document: Document
    score: int
    matches_in_text: int
    snippet: str
    snippet_offset: int


@dataclass
class SearchPage:
    """One page of results plus the bookkeeping needed to fetch more."""

    hits: list[SearchHit]
    total: int           # total matching documents across all pages
    page: int            # 1-indexed page returned
    page_size: int
    has_more: bool


_RG_BIN = shutil.which("rg")


# ─── Snippet extraction ───────────────────────────────────────────────────

def _extract_snippet(text: str, offset: int, context_words: int) -> str:
    """Take ``context_words`` words centred on ``offset``, half before/after."""
    if offset < 0:
        return text[:600]
    half = max(1, context_words // 2)

    # Words preceding the match — find the LAST `half` of them.
    pre_starts = [m.start() for m in re.finditer(r"\S+", text[:offset])]
    before_start = pre_starts[-half] if len(pre_starts) > half else 0

    # Words following the match — first `half` of them.
    after_end = len(text)
    for i, m in enumerate(re.finditer(r"\S+", text[offset:]), start=1):
        if i >= half:
            after_end = offset + m.end()
            break

    snippet = text[before_start:after_end].strip()
    if before_start > 0:
        snippet = "…" + snippet
    if after_end < len(text):
        snippet = snippet + "…"
    return snippet


# ─── ripgrep-backed scan ──────────────────────────────────────────────────

def _rg_candidates(
    query: str, docs_by_path: dict[Path, Document],
) -> list[tuple[Document, int]]:
    """Run ``rg -c`` once over the cache root, return (doc, count) hits.

    Filters to known doc cache paths so stray files in the cache root
    (left over from previous schemas) don't poison the results.
    """
    # Unique parent of all cache files — the cache root.
    cache_roots = {p.parents[2] for p in docs_by_path.keys()}
    if not cache_roots:
        return []
    root = next(iter(cache_roots))

    # Match documents containing ANY of the query TERMS (OR), not the literal
    # phrase. "veiligheid overlast" must hit docs about either word, ranked by
    # how many lines match — instead of returning 0 because that exact phrase
    # never appears verbatim. A short/single-token query is used as-is.
    terms = [t for t in re.split(r"\W+", query) if len(t) >= 3] or [query]
    pattern_args: list[str] = []
    for t in terms:
        pattern_args += ["-e", t]

    proc = subprocess.run(
        [
            _RG_BIN, "--count", "--ignore-case",
            "--no-heading", "--no-messages",
            "--type-add", "txt:*.txt", "--type", "txt",
            *pattern_args, str(root),
        ],
        capture_output=True, text=True,
    )
    if proc.returncode not in (0, 1):  # 1 = no matches, OK
        return []

    out: list[tuple[Document, int]] = []
    for line in proc.stdout.splitlines():
        path_str, _, count_str = line.rpartition(":")
        if not path_str or not count_str.isdigit():
            continue
        path = Path(path_str)
        doc = docs_by_path.get(path)
        if doc is None:
            continue
        out.append((doc, int(count_str)))
    return out


def _build_hit_with_snippet(
    doc: Document, count: int, query: str, context_words: int,
) -> SearchHit | None:
    """Re-open the doc, locate the strongest match, build a snippet."""
    try:
        text = load_text(doc)
    except OSError:
        return None
    # Snippet anchors on the first query TERM (not the literal phrase), so a
    # multi-word query still lands the snippet on a relevant passage.
    terms = [t for t in re.split(r"\W+", query) if len(t) >= 3] or [query]
    pat = re.compile("|".join(re.escape(t) for t in terms), re.I)
    m = pat.search(text)
    offset = m.start() if m else -1
    snippet = _extract_snippet(text, offset, context_words)
    return SearchHit(
        document=doc, score=count, matches_in_text=count,
        snippet=snippet, snippet_offset=offset,
    )


# ─── Python fallback (used when rg is missing) ────────────────────────────

def _compile_patterns(query: str) -> list[re.Pattern[str]]:
    full = re.compile(re.escape(query), re.I)
    tokens = [t for t in re.split(r"\W+", query) if len(t) >= 3]
    token_pats = [re.compile(rf"\b{re.escape(t)}\b", re.I) for t in tokens]
    return [full] + token_pats


def _score_text(text: str, patterns: list[re.Pattern[str]]) -> tuple[int, int, int]:
    full = patterns[0]
    full_hits = list(full.finditer(text))
    token_hits_total = 0
    first_token_offset: int | None = None
    for pat in patterns[1:]:
        for m in pat.finditer(text):
            token_hits_total += 1
            if first_token_offset is None:
                first_token_offset = m.start()
            break
    score = 3 * len(full_hits) + token_hits_total
    if full_hits:
        return score, len(full_hits), full_hits[0].start()
    if first_token_offset is not None:
        return score, 0, first_token_offset
    return 0, 0, -1


def _score_one_python(
    doc: Document, patterns: list[re.Pattern[str]], context_words: int,
) -> SearchHit | None:
    try:
        text = load_text(doc)
    except OSError:
        return None
    score, full_hits, offset = _score_text(text, patterns)
    if score <= 0:
        return None
    return SearchHit(
        document=doc, score=score, matches_in_text=full_hits,
        snippet=_extract_snippet(text, offset, context_words),
        snippet_offset=offset,
    )


# ─── Public entry point ───────────────────────────────────────────────────

SortKey = Literal["score", "gemeente", "date"]


def _cache_looks_populated(docs: list[Document]) -> bool:
    """Cheap probe: is the stripped-text cache actually present on disk?

    The rg search path greps the cache directory; if that cache is missing —
    or was built under a different ``$HOME`` than the current process — it is
    empty, and an empty rg result is then indistinguishable from a genuine
    no-match. Rather than walk thousands of files, check the cache root exists
    and probe a handful of expected cache paths.

    Returns True → an empty rg result is trustworthy ("no matches").
    Returns False → the cache is missing/unreadable; the caller must fall back
    to scanning the source corpus so a populated corpus never silently returns
    zero hits.
    """
    if not docs:
        return True  # nothing to search — an empty result is correct
    try:
        if not _cache_root().is_dir():
            return False
    except OSError:
        return False
    for d in docs[:8]:
        try:
            if _cache_path_for(d).exists():
                return True
        except OSError:
            continue
    return False


def _hit_sort_key(hit: SearchHit, mode: SortKey) -> tuple:
    """Sort key for SearchHit. Python sorts ascending; we flip when needed."""
    if mode == "gemeente":
        # Primary: gemeente alphabetical. Secondary: score desc. Tertiary: doc_id.
        return (hit.document.gemeente, -hit.score, hit.document.doc_id)
    if mode == "date":
        # Newest first. Docs without a date sort last.
        has_date = "0" if hit.document.date else "1"
        # ISO dates sort lexicographically, so negating is "z - char".
        # Easier: invert by mapping date string to a sortable descending key.
        return (has_date, _date_desc_key(hit.document.date), hit.document.doc_id)
    # default: score desc, doc_id asc.
    return (-hit.score, hit.document.doc_id)


def _scored_sort_key(
    pair: tuple[Document, int], mode: SortKey,
) -> tuple:
    """Sort key for the (doc, raw_count) pairs the rg path produces."""
    doc, count = pair
    if mode == "gemeente":
        return (doc.gemeente, -count, doc.doc_id)
    if mode == "date":
        has_date = "0" if doc.date else "1"
        return (has_date, _date_desc_key(doc.date), doc.doc_id)
    return (-count, doc.doc_id)


def _date_desc_key(date: str | None) -> str:
    """ISO ``yyyy-mm-dd`` → descending sort key (newer < older alphabetically)."""
    if not date or len(date) < 10:
        return "0000-00-00"
    # 9999 - year, etc. gives a string that sorts ascending = descending date.
    try:
        y, m, d = int(date[0:4]), int(date[5:7]), int(date[8:10])
    except ValueError:
        return "0000-00-00"
    return f"{9999 - y:04d}-{99 - m:02d}-{99 - d:02d}"


def search(
    docs: Iterable[Document],
    query: str,
    *,
    page: int = 1,
    page_size: int = 50,
    context_words: int = 100,
    sort: SortKey = "score",
    max_workers: int = 8,
) -> SearchPage:
    """Scan ``docs`` for ``query``, return one page of hits.

    ``sort`` selects the result ordering:

    * ``"score"`` (default): highest hit count first; doc_id tiebreaker.
    * ``"gemeente"``: alphabetical by municipality slug, then score desc.
      Pagination stays stable so paging walks through one gemeente at a
      time (Almere page 1 → Almere page 2 → …  → Zoetermeer last page).
    * ``"date"``: newest publication date first; docs without a parseable
      date sort last.

    All sorts are deterministic — score ties break by doc_id so the same
    hit never appears on two pages.
    """
    if not query.strip():
        return SearchPage(hits=[], total=0, page=page,
                          page_size=page_size, has_more=False)
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 1
    docs_list = list(docs)
    offset = (page - 1) * page_size

    if _RG_BIN is not None:
        docs_by_path: dict[Path, Document] = {
            _cache_path_for(d): d for d in docs_list
        }
        scored = _rg_candidates(query, docs_by_path)
        # An empty rg result is only trustworthy when the cache it greps is
        # actually populated. A missing/unreadable cache (e.g. built under a
        # different $HOME) yields the same empty result as a real no-match —
        # so only return the rg result when there ARE hits, or the cache is
        # genuinely present. Otherwise fall through to the source scan below.
        if scored or _cache_looks_populated(docs_list):
            scored.sort(key=lambda p: _scored_sort_key(p, sort))
            total = len(scored)
            if offset >= total:
                return SearchPage(hits=[], total=total, page=page,
                                  page_size=page_size, has_more=False)
            window = scored[offset : offset + page_size]
            hits: list[SearchHit] = []
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                for hit in ex.map(
                    lambda pair: _build_hit_with_snippet(
                        pair[0], pair[1], query, context_words,
                    ),
                    window, chunksize=4,
                ):
                    if hit is not None:
                        hits.append(hit)
            hits.sort(key=lambda h: _hit_sort_key(h, sort))
            return SearchPage(
                hits=hits, total=total, page=page, page_size=page_size,
                has_more=(offset + page_size) < total,
            )

    # ── Python source scan (no ripgrep, or cache empty/unreadable) ──
    patterns = _compile_patterns(query)
    all_hits: list[SearchHit] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for hit in ex.map(
            lambda d: _score_one_python(d, patterns, context_words),
            docs_list, chunksize=64,
        ):
            if hit is not None:
                all_hits.append(hit)
    all_hits.sort(key=lambda h: _hit_sort_key(h, sort))
    total = len(all_hits)
    window = all_hits[offset : offset + page_size]
    return SearchPage(
        hits=window, total=total, page=page, page_size=page_size,
        has_more=(offset + page_size) < total,
    )
