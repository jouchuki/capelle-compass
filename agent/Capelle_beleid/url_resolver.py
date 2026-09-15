"""
Look up real begrotingsapp.nl URLs from ``url_catalog.json``.

WHY THIS EXISTS:
    The naive templater in :mod:`build_vectordb` used to assemble URLs
    from ``{BASE_URL}/{doc_type}-{year}/{segment}/{slug}`` where
    ``segment`` was inferred from the filename slug (programma /
    paragraaf / bestuur / bestanden). In reality the begrotingsapp SaaS
    serves **every** section type under ``/programma/...``, with a
    ``paragraaf-`` prefix on the slug where applicable. The three
    legacy segments (/paragraaf/, /bestuur/, /bestanden/) return 404 —
    65% of the originally-indexed URLs were dead links.

    ``url_catalog.json`` is the authoritative list of real URLs,
    discovered via begrotingsapp's sitemap + per-document landing-page
    crawl (scripts that live under ``capelle-deploy/deploy/``). Each
    entry is a tuple of ``(url, doc_type, year, doc_suffix, section,
    slug, http_status)``.

HOW IT IS USED:
    ``resolve_source_url(doc_type, year, sub_index, slug)`` returns the
    real URL if the catalog has a match, otherwise ``""`` — never a
    fabricated guess. Indexer writes the return value directly into
    chunk metadata.

REFRESHING THE CATALOG:
    The catalog only covers documents that existed at crawl time. When
    Capelle publishes a new begroting / voorjaarsnota / etc., re-run
    the crawler in ``capelle-deploy/scripts/...`` and drop the fresh
    JSON back into ``url_catalog.json``. Old entries remain valid.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_CATALOG_PATH = Path(__file__).resolve().parent / "url_catalog.json"


class _Catalog:
    """Indexed view of ``url_catalog.json`` with O(1) slug lookup.

    The same slug may appear under multiple ``section`` values for a
    single doc_key (e.g. the indexer uses ``paragraaf-weerstandsvermogen``
    but the catalog has the real URL under ``/programma/paragraaf-
    weerstandsvermogen``). We always prefer ``http_status == 200`` and
    ties broken by catalog order.
    """

    def __init__(self, path: Path = _CATALOG_PATH) -> None:
        if not path.exists():
            self._by_key: dict[tuple[str, str], list[dict]] = {}
            return
        data = json.loads(path.read_text())
        by_key: dict[tuple[str, str], list[dict]] = {}
        for row in data.get("urls", []):
            doc_type = row.get("doc_type") or ""
            year = row.get("year")
            suffix = row.get("doc_suffix") or ""
            slug = row.get("slug") or ""
            if not (doc_type and year and slug):
                continue
            doc_key = f"{doc_type}-{year}{suffix}"
            by_key.setdefault((doc_key, slug), []).append(row)
        self._by_key = by_key

    def lookup(self, doc_key: str, slug: str) -> str:
        """Return the best URL for ``(doc_key, slug)`` or an empty string.

        Fallback rules, in order:
            1. exact match, status 200
            2. exact match, any status
            3. ``paragraaf-<slug>`` prefix match, status 200
               (the indexer often drops the paragraaf- prefix)
            4. empty string — no fabrication
        """
        candidates = self._by_key.get((doc_key, slug), [])
        for row in candidates:
            if row.get("http_status") == 200:
                return str(row.get("url") or "")
        if candidates:
            return str(candidates[0].get("url") or "")
        if not slug.startswith("paragraaf-"):
            paragraaf_slug = f"paragraaf-{slug}"
            for row in self._by_key.get((doc_key, paragraaf_slug), []):
                if row.get("http_status") == 200:
                    return str(row.get("url") or "")
        return ""


@lru_cache(maxsize=1)
def _catalog() -> _Catalog:
    """Load the catalog once per process."""
    return _Catalog()


def resolve_source_url(
    doc_type: str,
    year: int,
    sub_index: int | None,
    slug: str,
) -> str:
    """
    Return the real begrotingsapp URL for a given (doc_type, year, slug).

    Returns ``""`` if the catalog does not know this slug — the indexer
    writes the empty string into ``source_url`` so the frontend renders
    a dash in the sources footer rather than a dead link. Never
    guesses.

    ``sub_index`` is the trailing digit appended to some document
    types (e.g. ``bestuursrapportage-2025-1``); when present we build
    ``doc_key = "<doc_type>-<year>-<sub_index>"``.
    """
    suffix = f"-{sub_index}" if sub_index is not None else ""
    doc_key = f"{doc_type}-{year}{suffix}"
    return _catalog().lookup(doc_key, slug)
