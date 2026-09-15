#!/usr/bin/env python3
"""
Download every CVDR (Centrale Voorziening Decentrale Regelgeving) regeling
that's currently in force for the selected municipalities, into the local
corpus under ``data/groeikernen/{slug}/verordeningen/``.

Source: ``lokaleregelgeving.overheid.nl`` (the public CVDR search). The
script paginates the search by gemeente, harvests the
``CVDR<id>/<version>`` references, and saves each regeling's full detail
HTML to disk. Idempotent: existing files are skipped unless ``--force``.

For each gemeente we write:
  data/groeikernen/{slug}/verordeningen/
  ├── index.json                       # {id, version, title, url, fetched_at}
  └── html/
      └── CVDR{id}_v{version}.html     # raw detail page (regeling tekst inline)

The companion ``clean_cvdr.py`` reads from ``html/`` and writes cleaned
Markdown into a sibling ``md/`` folder.

Default search uses ``datumrange=op`` so we only pull regelingen that are
currently in force today — i.e. the "live" set, not the historical archive.

Usage
-----
    python3 scripts/download_cvdr.py
    python3 scripts/download_cvdr.py --all-gemeenten
    python3 scripts/download_cvdr.py --gemeente capelle-aan-den-ijssel
    python3 scripts/download_cvdr.py --force
    python3 scripts/download_cvdr.py --max-per-gemeente 50   # smoke test
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from unicodedata import normalize
from typing import Any
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = REPO_ROOT / "data" / "groeikernen"

BASE_HOST = "https://lokaleregelgeving.overheid.nl"
SEARCH_PATH = "/ZoekResultaat"
SEARCH_PARAMS_STATIC = "datumrange=op&indeling="

# CBS-code → display name used in the CVDR search filter. The display name
# is the official BAG/CBS spelling; URL-encoding handled by the fetcher.
GROEIKERNEN: tuple[tuple[str, str, str], ...] = (
    ("GM0502", "capelle-aan-den-ijssel", "Capelle aan den IJssel"),
    ("GM0034", "almere",                 "Almere"),
    ("GM0637", "zoetermeer",             "Zoetermeer"),
    ("GM0356", "nieuwegein",             "Nieuwegein"),
    ("GM0439", "purmerend",              "Purmerend"),
    ("GM0995", "lelystad",               "Lelystad"),
    ("GM0321", "houten",                 "Houten"),
)

MUNICIPALITIES_PATH = REPO_ROOT / "data" / "nederlandse_gemeenten.json"

DISPLAY_NAME_OVERRIDES: dict[str, str] = {
    "bergen-limburg": "Bergen (L)",
    "bergen-noord-holland": "Bergen (NH)",
    "dantumadeel": "Dantumadiel",
    "de-friese-meren": "De Fryske Marren",
    "den-haag": "'s-Gravenhage",
    "nuenen-c-a": "Nuenen, Gerwen en Nederwetten",
    "tietjerksteradeel": "Tytsjerksteradiel",
}

# CVDR result-page selectors / regexes — kept here as constants per the
# "no magic strings" rule and so a brittle scrape can be repaired in one
# place when the page template inevitably shifts.
RESULT_HREF_RE = re.compile(r'href="(/CVDR\d+/\d+)"')
RESULT_BLOCK_RE = re.compile(
    r'<h2 class="result--title[^"]*"><a href="(/CVDR\d+/\d+)">([^<]+)</a>',
    re.S,
)
LAST_PAGE_RE = re.compile(r'page=(\d+)"[^>]*>(?:[^<]*Laatste|[^<]*Last)', re.S)

REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 3
RETRY_DELAY_SECONDS = 2.0
SEARCH_CONCURRENCY = 4
DETAIL_CONCURRENCY = 6
INTER_REQUEST_DELAY = 0.15  # crude rate-limit between detail fetches
USER_AGENT = (
    "Mozilla/5.0 (compatible; KompasMunicipalCorpus/1.0; "
    "+https://lokaleregelgeving.overheid.nl/)"
)


def _slugify(name: str) -> str:
    """Dutch municipality name -> stable URL/file slug."""
    ascii_name = normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug


def _load_all_gemeenten(*, include_special: bool = False) -> tuple[tuple[str, str, str], ...]:
    """Load all municipalities from the repo's canonical municipality list.

    The source contains 345 records: 342 regular Dutch municipalities plus
    three Caribbean special municipalities. By default the national corpus
    follows the regular municipality scope.
    """
    data = json.loads(MUNICIPALITIES_PATH.read_text(encoding="utf-8"))
    records = [
        record for record in data.get("records", [])
        if include_special or not record.get("special_municipality")
    ]
    base_counts: dict[str, int] = {}
    for record in records:
        base = _slugify(str(record["name"]))
        base_counts[base] = base_counts.get(base, 0) + 1

    out: list[tuple[str, str, str]] = []
    seen_slugs: set[str] = set()
    for record in records:
        if record.get("special_municipality") and not include_special:
            continue
        name = str(record["name"])
        base_slug = _slugify(name)
        slug = base_slug
        if base_counts[base_slug] > 1:
            slug = f"{base_slug}-{_slugify(str(record['province']))}"
        if slug in seen_slugs:
            slug = f"{slug}-{int(record['cbs_code']):04d}"
        if slug in seen_slugs:
            raise ValueError(f"duplicate gemeente slug generated: {slug}")
        seen_slugs.add(slug)
        code = f"GM{int(record['cbs_code']):04d}"
        out.append((code, slug, DISPLAY_NAME_OVERRIDES.get(slug, name)))
    return tuple(out)


def _select_gemeenten(
    requested: list[str] | None,
    *,
    all_gemeenten: bool,
    include_special: bool,
) -> tuple[tuple[str, str, str], ...]:
    all_records = _load_all_gemeenten(include_special=include_special)
    if requested:
        requested_set = set(requested)
        selection = tuple(g for g in all_records if g[1] in requested_set)
        missing = sorted(requested_set - {g[1] for g in selection})
        if missing:
            raise ValueError(
                f"unknown gemeente slug(s): {missing}. "
                f"Use one from {MUNICIPALITIES_PATH.relative_to(REPO_ROOT)}."
            )
        return selection
    return all_records if all_gemeenten else GROEIKERNEN


@dataclass
class Regeling:
    """One entry in the per-gemeente index.json."""

    cvdr_id: str
    version: str
    title: str
    detail_url: str


@dataclass
class GemeenteResult:
    slug: str
    code: str
    display: str
    total_pages_seen: int = 0
    regelingen: list[Regeling] = field(default_factory=list)
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


# ─── HTTP helpers ─────────────────────────────────────────────────────────

def _get_html_sync(url: str) -> str | None:
    """Fetch and return body text, or None on failure."""
    for attempt in range(REQUEST_RETRIES):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:  # noqa: S310 - public government source
                raw = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
        except Exception:  # noqa: BLE001
            if attempt < REQUEST_RETRIES - 1:
                time.sleep(RETRY_DELAY_SECONDS * (attempt + 1))
    return None


async def _get_html(url: str) -> str | None:
    return await asyncio.to_thread(_get_html_sync, url)


def _search_url(display_name: str, page: int) -> str:
    """Build a page URL that mirrors the user's search."""
    gemeente = quote_plus(display_name)
    suffix = f"&page={page}" if page > 1 else ""
    return f"{BASE_HOST}{SEARCH_PATH}?{SEARCH_PARAMS_STATIC}&gemeenten={gemeente}{suffix}"


def _detail_url(href: str) -> str:
    return f"{BASE_HOST}{href}"


# ─── Search parsing ───────────────────────────────────────────────────────

def _parse_results(html: str) -> list[Regeling]:
    seen: set[str] = set()
    out: list[Regeling] = []
    for m in RESULT_BLOCK_RE.finditer(html):
        href, title = m.group(1), m.group(2).strip()
        if href in seen:
            continue
        seen.add(href)
        # /CVDR<id>/<version>
        match = re.match(r"/CVDR(\d+)/(\d+)", href)
        if not match:
            continue
        out.append(Regeling(
            cvdr_id=match.group(1),
            version=match.group(2),
            title=title,
            detail_url=_detail_url(href),
        ))
    return out


def _parse_last_page(html: str) -> int:
    """Find the highest page number linked from the search results.

    Hrefs in the result HTML are HTML-entity-encoded (``&amp;page=N``),
    so we look for ``page=N`` directly rather than gating on the
    preceding char.
    """
    matches = re.findall(r"\bpage=(\d+)", html)
    if not matches:
        return 1
    return max(int(p) for p in matches)


# ─── Per-gemeente pipeline ────────────────────────────────────────────────

async def _harvest_search(
    display_name: str,
    *,
    max_pages: int | None = None,
) -> tuple[list[Regeling], int, list[str]]:
    """
    Paginate the CVDR search for one gemeente; return the de-duplicated
    regeling list plus the count of pages actually fetched plus any errors.
    """
    errors: list[str] = []
    sem = asyncio.Semaphore(SEARCH_CONCURRENCY)

    # Fetch page 1 first so we can read total page count, then fan out.
    page1_html = await _get_html(_search_url(display_name, 1))
    if not page1_html:
        return [], 0, [f"page1 fetch failed for {display_name!r}"]
    last_page = _parse_last_page(page1_html)
    if max_pages is not None:
        last_page = min(last_page, max_pages)

    all_regelingen: dict[str, Regeling] = {
        r.cvdr_id: r for r in _parse_results(page1_html)
    }

    async def fetch_page(p: int) -> None:
        async with sem:
            html = await _get_html(_search_url(display_name, p))
            if not html:
                errors.append(f"page {p} fetch failed for {display_name!r}")
                return
            for r in _parse_results(html):
                # Later versions of the same regeling override earlier ones.
                existing = all_regelingen.get(r.cvdr_id)
                if existing is None or int(r.version) > int(existing.version):
                    all_regelingen[r.cvdr_id] = r

    await asyncio.gather(*[fetch_page(p) for p in range(2, last_page + 1)])
    return list(all_regelingen.values()), last_page, errors


async def _download_detail(
    regeling: Regeling, dest: Path, sem: asyncio.Semaphore, *, force: bool,
) -> str:
    """Fetch + save one regeling. Returns "ok" / "skipped" / "failed:<reason>"."""
    if dest.exists() and not force:
        return "skipped"
    async with sem:
        await asyncio.sleep(INTER_REQUEST_DELAY)
        html = await _get_html(regeling.detail_url)
        if html is None:
            return f"failed:fetch {regeling.detail_url}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html, encoding="utf-8")
        return "ok"


async def _process_gemeente(
    code: str, slug: str, display: str,
    *, force: bool, max_per_gemeente: int | None, max_pages: int | None,
) -> GemeenteResult:
    result = GemeenteResult(slug=slug, code=code, display=display)
    print(f"[{slug}] harvesting search results...", flush=True)
    regelingen, last_page, errors = await _harvest_search(
        display, max_pages=max_pages,
    )
    result.total_pages_seen = last_page
    result.errors.extend(errors)
    if last_page == 0 and errors:
        result.failed += 1
        print(
            f"[{slug}] WARN: search failed; keeping existing index/html untouched",
            flush=True,
        )
        return result
    if max_per_gemeente is not None:
        regelingen = regelingen[:max_per_gemeente]
    result.regelingen = regelingen
    print(
        f"[{slug}] {len(regelingen)} regelingen across {last_page} page(s)",
        flush=True,
    )

    gem_dir = OUT_ROOT / slug / "verordeningen"
    html_dir = gem_dir / "html"
    sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

    async def one(r: Regeling) -> None:
        fname = f"CVDR{r.cvdr_id}_v{r.version}.html"
        outcome = await _download_detail(r, html_dir / fname, sem, force=force)
        if outcome == "ok":
            result.downloaded += 1
        elif outcome == "skipped":
            result.skipped += 1
        else:
            result.failed += 1
            result.errors.append(outcome)

    if regelingen:
        await asyncio.gather(*[one(r) for r in regelingen])

    # index.json lives at the gemeente root (not inside html/ or md/) so
    # downstream consumers don't have to know which subfolder they're in.
    index_path = gem_dir / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps({
        "code": code,
        "slug": slug,
        "display": display,
        "fetched_at": int(time.time()),
        "total_pages_seen": last_page,
        "regelingen": [
            {
                "cvdr_id": r.cvdr_id,
                "version": r.version,
                "title": r.title,
                "detail_url": r.detail_url,
                "file": f"CVDR{r.cvdr_id}_v{r.version}.html",
            }
            for r in regelingen
        ],
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(
        f"[{slug}] downloaded={result.downloaded} skipped={result.skipped} "
        f"failed={result.failed}",
        flush=True,
    )
    return result


async def _run(
    selection: tuple[tuple[str, str, str], ...],
    *,
    force: bool,
    max_per_gemeente: int | None,
    max_pages: int | None,
    gemeente_concurrency: int,
) -> list[GemeenteResult]:
    sem = asyncio.Semaphore(max(1, gemeente_concurrency))

    async def one(code: str, slug: str, display: str) -> GemeenteResult:
        async with sem:
            return await _process_gemeente(
                code, slug, display,
                force=force,
                max_per_gemeente=max_per_gemeente,
                max_pages=max_pages,
            )

    return await asyncio.gather(*[one(code, slug, display) for code, slug, display in selection])


def _write_manifest(results: list[GemeenteResult]) -> Path:
    path = OUT_ROOT / "_shared" / "cvdr_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "generated_unix": int(time.time()),
        "source": "https://lokaleregelgeving.overheid.nl/",
        "scope_count": len(results),
        "gemeenten": [
            {
                "code": r.code,
                "slug": r.slug,
                "display": r.display,
                "regelingen": len(r.regelingen),
                "downloaded": r.downloaded,
                "skipped": r.skipped,
                "failed": r.failed,
                "errors_first5": r.errors[:5],
            }
            for r in results
        ],
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--gemeente", action="append",
        help="Slug to fetch. Repeatable. Matches data/nederlandse_gemeenten.json.",
    )
    parser.add_argument(
        "--all-gemeenten", action="store_true",
        help="Fetch all regular Dutch municipalities from data/nederlandse_gemeenten.json.",
    )
    parser.add_argument(
        "--include-special", action="store_true",
        help="With --all-gemeenten, also include the three Caribbean special municipalities.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-download regelingen even if the HTML already exists.",
    )
    parser.add_argument(
        "--max-per-gemeente", type=int, default=None,
        help="Cap regelingen per gemeente (useful for smoke tests).",
    )
    parser.add_argument(
        "--max-pages", type=int, default=None,
        help="Cap search-result pages per gemeente (useful for smoke tests).",
    )
    parser.add_argument(
        "--gemeente-concurrency", type=int, default=4,
        help="How many municipalities to process at once (default: 4).",
    )
    parser.add_argument(
        "--start-at",
        help="Resume from this gemeente slug within the selected, sorted list.",
    )
    args = parser.parse_args()

    try:
        selection = _select_gemeenten(
            args.gemeente,
            all_gemeenten=args.all_gemeenten,
            include_special=args.include_special,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.start_at:
        slugs = [g[1] for g in selection]
        if args.start_at not in slugs:
            print(f"--start-at slug not in selected gemeenten: {args.start_at}", file=sys.stderr)
            return 2
        selection = selection[slugs.index(args.start_at):]

    print(
        f"CVDR download — gemeenten={len(selection)} "
        f"scope={'all' if args.all_gemeenten else 'groeikernen'} "
        f"force={args.force} max_per_gemeente={args.max_per_gemeente} "
        f"max_pages={args.max_pages} gemeente_concurrency={args.gemeente_concurrency}",
        flush=True,
    )
    results = asyncio.run(_run(
        selection, force=args.force, max_per_gemeente=args.max_per_gemeente,
        max_pages=args.max_pages,
        gemeente_concurrency=args.gemeente_concurrency,
    ))
    manifest = _write_manifest(results)
    print(f"\nmanifest: {manifest.relative_to(REPO_ROOT)}")
    failed = sum(r.failed for r in results)
    if failed:
        print(f"WARN: {failed} downloads failed across gemeenten")
        return 1
    print("DONE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
