#!/usr/bin/env python3
"""
Download official municipal bekendmakingen (Gemeenteblad regelgeving) for
the selected municipalities from ``zoek.officielebekendmakingen.nl``, into the local
corpus under ``data/groeikernen/{slug}/bekendmakingen/``.

Source: KOOP's public search (``zoek.officielebekendmakingen.nl``). The
site's ``sf=...`` facet parameters are decorative-only in direct URLs, so
we scope the real search query with ``dt.creator`` and ``dt.type``. For each
gemeente we paginate the four Gemeenteblad rubrieken that carry regelgeving:

  - Algemeen verbindend voorschrift (verordening)  ← actual bylaws
  - Beleidsregel                                    ← policy rules
  - Ander besluit van algemene strekking            ← tariefbesluiten etc.
  - Delegatie- of mandaatbesluit                    ← authority delegations

The full Gemeenteblad is mostly permits + traffic + misc; server-side
``dt.type`` filtering keeps the download focused on the regelgeving subset.

For each gemeente we write:
  data/groeikernen/{slug}/bekendmakingen/
  ├── index.json                # {pub_id, rubriek, date, title, url}
  └── html/
      └── gmb-YYYY-NNNNNN.html  # raw publication page

Companion ``clean_bekendmakingen.py`` (TBD) will mirror ``clean_cvdr.py`` and
write Markdown into a sibling ``md/`` folder.

Usage
-----
    python3 scripts/download_bekendmakingen.py
    python3 scripts/download_bekendmakingen.py --all-gemeenten
    python3 scripts/download_bekendmakingen.py --gemeente capelle-aan-den-ijssel
    python3 scripts/download_bekendmakingen.py --max-per-gemeente 20  # smoke test
    python3 scripts/download_bekendmakingen.py --force
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
from urllib.parse import quote
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = REPO_ROOT / "data" / "groeikernen"

BASE_HOST = "https://zoek.officielebekendmakingen.nl"
SEARCH_PATH = "/resultaten"

# Same 7 groeikernen as the CVDR + OpenSpending scripts. ``display`` is
# the official CBS/BAG spelling used for ``dt.creator`` matching.
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

# Target rubrieken — compared case-insensitively against the lowercase text
# rendered inside ``<h2 class="result--title">`` on each listing row.
TARGET_RUBRIEKEN: frozenset[str] = frozenset({
    "algemeen verbindend voorschrift (verordening)",
    "beleidsregel",
    "ander besluit van algemene strekking",
    "delegatie- of mandaatbesluit",
})

PAGE_SIZE = 50  # 10 / 20 / 50 allowed — max to minimise pagination cost
SEARCH_CONCURRENCY = 1
DETAIL_CONCURRENCY = 6
SEARCH_PAGE_DELAY = 0.35
FAILED_SEARCH_PAGE_RETRY_DELAY = 15.0
INTER_REQUEST_DELAY = 0.20  # polite-ish to KOOP
REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 5
RETRY_DELAY_SECONDS = 3.0
USER_AGENT = (
    "Mozilla/5.0 (compatible; KompasMunicipalCorpus/1.0; "
    "+https://zoek.officielebekendmakingen.nl/)"
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

# Per-row extraction. The listing HTML renders each result as:
#   <h2 class="result--title "><a href="/gmb-YYYY-NNNNNN.html">RUBRIEK</a></h2>
#   <p><a href="/gmb-YYYY-NNNNNN.html" class="result--subtitle">TITLE</a></p>
#   ...<dt>Datum publicatie</dt><dd>DD-MM-YYYY</dd>...
# The back-reference \1 ensures the date we capture belongs to the same row.
ROW_RE = re.compile(
    r'<h2 class="result--title[^"]*"><a href="/(gmb-\d{4}-\d+)\.html?">'
    r'([^<]+)</a></h2>\s*'
    r'<p><a href="/\1\.html?"\s+class="result--subtitle">([^<]+)</a></p>'
    r'.*?<dt>Datum publicatie</dt>\s*<dd>(\d{2}-\d{2}-\d{4})</dd>',
    re.S,
)

# Total result-count caption — preferred pagination signal because the
# page also contains unrelated ``pagina=`` references (sidebar/footer
# nav) that overshoot the actual result set.
#   "Zoekresultaten 1 - 50 van de 4.046 resultaten"
TOTAL_RESULTS_RE = re.compile(
    r"van\s+de\s+(\d{1,3}(?:\.\d{3})*)\s+resultaten", re.I,
)

# Fallback: highest ``pagina=`` value in the HTML. Used only if the total
# caption is absent. HTML-entity-encoded (``&amp;pagina=N``), so anchor on
# word-boundary not on ``[?&]``.
LAST_PAGINA_RE = re.compile(r"\bpagina=(\d+)")


# ─── Models ────────────────────────────────────────────────────────────────

@dataclass
class Publication:
    """One entry in the per-gemeente bekendmakingen index.json."""

    pub_id: str          # e.g. "gmb-2026-224348"
    rubriek: str         # human label, e.g. "Beleidsregel"
    detail_url: str
    title: str | None = None
    date: str | None = None  # ISO yyyy-mm-dd if parseable


@dataclass
class GemeenteResult:
    slug: str
    code: str
    display: str
    publications: list[Publication] = field(default_factory=list)
    pages_seen: int = 0
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


# ─── HTTP helpers ──────────────────────────────────────────────────────────

def _get_html_sync(url: str) -> str | None:
    """Fetch and return body text, or None on failure."""
    last_exc: Exception | None = None
    for attempt in range(REQUEST_RETRIES):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:  # noqa: S310 - public government source
                raw = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < REQUEST_RETRIES - 1:
                time.sleep(RETRY_DELAY_SECONDS * (attempt + 1))
    if last_exc is not None:
        print(
            f"WARN: fetch failed after {REQUEST_RETRIES} attempts: {url} "
            f"({type(last_exc).__name__}: {last_exc})",
            file=sys.stderr,
            flush=True,
        )
    return None


async def _get_html(url: str) -> str | None:
    return await asyncio.to_thread(_get_html_sync, url)


async def _get_search_html(url: str) -> str | None:
    await asyncio.sleep(SEARCH_PAGE_DELAY)
    return await _get_html(url)


def _cql_for_gemeente(display: str, rubriek: str | None = None) -> str:
    """Build the q= CQL clause scoped to one gemeente's Gemeenteblad."""
    # Mirror the live UI's exact CQL shape — confirmed against the URL the
    # user supplied. The escaped double-quotes around the gemeente name
    # matter; KOOP rejects unquoted creator values.
    q = (
        '(c.product-area=="officielepublicaties")'
        'and(((w.publicatienaam=="Gemeenteblad")))'
        f' AND dt.creator=="{display}"'
    )
    if rubriek is not None:
        q += f' AND dt.type=="{rubriek}"'
    return q


def _search_url(display: str, pagina: int, rubriek: str | None = None) -> str:
    q = quote(_cql_for_gemeente(display, rubriek), safe="")
    return (
        f"{BASE_HOST}{SEARCH_PATH}"
        f"?q={q}"
        f"&col=AlleBekendmakingen"
        f"&svel=Publicatiedatum"
        f"&svol=Aflopend"
        f"&pg={PAGE_SIZE}"
        f"&pagina={pagina}"
    )


def _detail_url(pub_id: str) -> str:
    return f"{BASE_HOST}/{pub_id}.html"


# ─── Search parsing ────────────────────────────────────────────────────────

def _iso_date(dmy: str) -> str:
    """``12-05-2026`` → ``2026-05-12``. Returns input unchanged if malformed."""
    parts = dmy.split("-")
    if len(parts) == 3 and len(parts[2]) == 4:
        return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return dmy


def _parse_rows(html: str) -> list[Publication]:
    """Extract (pub_id, rubriek, title, date) per result row, in source order."""
    out: list[Publication] = []
    for m in ROW_RE.finditer(html):
        pub_id, rubriek_raw, title_raw, date_dmy = m.groups()
        out.append(Publication(
            pub_id=pub_id,
            rubriek=rubriek_raw.strip(),
            detail_url=_detail_url(pub_id),
            title=title_raw.strip(),
            date=_iso_date(date_dmy),
        ))
    return out


def _parse_last_pagina(html: str) -> int:
    """How many listing pages cover all results.

    Prefer the visible total-count caption ("van de N resultaten") because
    the page carries unrelated ``pagina=`` links (e.g. browse-all sidebars)
    that overshoot the actual result set by 10×+ on small searches. Fall
    back to highest ``pagina=`` only if the caption is missing.
    """
    cap = TOTAL_RESULTS_RE.search(html)
    if cap is not None:
        total = int(cap.group(1).replace(".", ""))
        return max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    matches = LAST_PAGINA_RE.findall(html)
    if not matches:
        return 1
    return max(int(p) for p in matches)


# ─── Per-gemeente harvest ──────────────────────────────────────────────────

def _is_regelgeving(rubriek: str) -> bool:
    return rubriek.strip().lower() in TARGET_RUBRIEKEN


async def _harvest_rubriek(
    display: str,
    rubriek: str,
    *,
    max_pages: int | None = None,
) -> tuple[list[Publication], int, list[str]]:
    """
    Paginate one Gemeenteblad regelgeving rubriek for one gemeente.
    """
    errors: list[str] = []
    failed_pages: set[int] = set()
    sem = asyncio.Semaphore(SEARCH_CONCURRENCY)

    page1_url = _search_url(display, 1, rubriek)
    page1_html = await _get_search_html(page1_url)
    if page1_html is None:
        return [], 0, [f"page1 fetch failed for {display!r}/{rubriek!r}: {page1_url}"]
    last_pagina = _parse_last_pagina(page1_html)
    if max_pages is not None:
        last_pagina = min(last_pagina, max_pages)

    by_id: dict[str, Publication] = {}
    for pub in _parse_rows(page1_html):
        if _is_regelgeving(pub.rubriek):
            by_id.setdefault(pub.pub_id, pub)

    async def fetch_page(p: int) -> None:
        async with sem:
            html = await _get_search_html(_search_url(display, p, rubriek))
            if html is None:
                failed_pages.add(p)
                return
            for pub in _parse_rows(html):
                if _is_regelgeving(pub.rubriek):
                    by_id.setdefault(pub.pub_id, pub)

    if last_pagina > 1:
        await asyncio.gather(*[fetch_page(p) for p in range(2, last_pagina + 1)])
    for p in sorted(failed_pages):
        await asyncio.sleep(FAILED_SEARCH_PAGE_RETRY_DELAY)
        html = await _get_search_html(_search_url(display, p, rubriek))
        if html is None:
            errors.append(f"page {p} fetch failed for {display!r}/{rubriek!r}")
            continue
        for pub in _parse_rows(html):
            if _is_regelgeving(pub.rubriek):
                by_id.setdefault(pub.pub_id, pub)

    return list(by_id.values()), last_pagina, errors


async def _harvest_gemeente(
    display: str,
    *,
    max_pages: int | None = None,
) -> tuple[list[Publication], int, list[str]]:
    """
    Paginate the target Gemeenteblad rubrieken for one gemeente.
    """
    by_id: dict[str, Publication] = {}
    pages_seen = 0
    errors: list[str] = []
    for rubriek in sorted(TARGET_RUBRIEKEN):
        pubs, pages, rubriek_errors = await _harvest_rubriek(
            display,
            rubriek,
            max_pages=max_pages,
        )
        pages_seen += pages
        errors.extend(rubriek_errors)
        for pub in pubs:
            by_id.setdefault(pub.pub_id, pub)
    return list(by_id.values()), pages_seen, errors


# ─── Detail download ───────────────────────────────────────────────────────

async def _download_detail(
    pub: Publication, dest: Path, sem: asyncio.Semaphore, *, force: bool,
) -> str:
    """Returns ``ok`` / ``skipped`` / ``failed:<reason>``."""
    if dest.exists() and not force:
        return "skipped"
    async with sem:
        await asyncio.sleep(INTER_REQUEST_DELAY)
        html = await _get_html(pub.detail_url)
        if html is None:
            return f"failed:fetch {pub.detail_url}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html, encoding="utf-8")
        return "ok"


# ─── Per-gemeente pipeline ─────────────────────────────────────────────────

def _load_prior_index(path: Path) -> dict[str, Publication]:
    """Read a previous run's index.json, return prior publications keyed by id."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, Publication] = {}
    for p in data.get("publications", []):
        out[p["pub_id"]] = Publication(
            pub_id=p["pub_id"],
            rubriek=p.get("rubriek", ""),
            detail_url=p.get("detail_url", _detail_url(p["pub_id"])),
            title=p.get("title"),
            date=p.get("date"),
        )
    return out


async def _process_gemeente(
    code: str, slug: str, display: str,
    *, force: bool, max_per_gemeente: int | None, max_pages: int | None,
) -> GemeenteResult:
    result = GemeenteResult(slug=slug, code=code, display=display)
    gem_dir = OUT_ROOT / slug / "bekendmakingen"
    html_dir = gem_dir / "html"
    index_path = gem_dir / "index.json"

    # Accumulate publications across runs — KOOP's search returns slightly
    # different (and incomplete) result sets on consecutive calls due to
    # rate-limiting and indexing state, so we union with whatever a previous
    # run discovered. ``--force`` discards the prior index and starts fresh.
    prior = {} if force else _load_prior_index(index_path)
    if prior:
        print(f"[{slug}] loaded {len(prior)} publications from prior index",
              flush=True)

    print(f"[{slug}] paginating Gemeenteblad...", flush=True)
    fresh, last_pagina, errors = await _harvest_gemeente(display, max_pages=max_pages)
    result.pages_seen = last_pagina
    result.errors.extend(errors)
    if last_pagina == 0 and errors:
        result.failed += 1
        print(
            f"[{slug}] WARN: search failed; keeping existing index/html untouched",
            flush=True,
        )
        return result
    print(
        f"[{slug}] this run found {len(fresh)} regelgeving-publication(s) "
        f"across {last_pagina} listing page(s)",
        flush=True,
    )

    # Union: keep prior entries, add any fresh ones not already present.
    merged = dict(prior)
    for pub in fresh:
        merged.setdefault(pub.pub_id, pub)
    new_count = len(merged) - len(prior)
    print(
        f"[{slug}] union: prior={len(prior)} + new={new_count} = {len(merged)} total",
        flush=True,
    )

    publications = list(merged.values())
    if max_per_gemeente is not None:
        publications = publications[:max_per_gemeente]
    result.publications = publications

    # Refuse to clobber a non-empty existing index with an empty union —
    # belt-and-braces against the bug above resurfacing.
    if not publications and prior and not force:
        result.errors.append(
            f"refusing to overwrite existing index with {len(prior)} entries "
            f"after empty harvest (likely transient KOOP failure)"
        )
        print(
            f"[{slug}] WARN: empty harvest with {len(prior)} prior entries — "
            f"keeping prior index.json intact (re-run, or pass --force)",
            flush=True,
        )
        return result
    sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

    async def one(pub: Publication) -> None:
        dest = html_dir / f"{pub.pub_id}.html"
        outcome = await _download_detail(pub, dest, sem, force=force)
        if outcome == "ok":
            result.downloaded += 1
        elif outcome == "skipped":
            result.skipped += 1
        else:
            result.failed += 1
            result.errors.append(outcome)

    if publications:
        await asyncio.gather(*[one(p) for p in publications])

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps({
        "code": code,
        "slug": slug,
        "display": display,
        "fetched_at": int(time.time()),
        "rubrieken": sorted(TARGET_RUBRIEKEN),
        "publications": [
            {
                "pub_id": p.pub_id,
                "rubriek": p.rubriek,
                "title": p.title,
                "date": p.date,
                "detail_url": p.detail_url,
                "file": f"{p.pub_id}.html",
            }
            for p in publications
        ],
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(
        f"[{slug}] total={len(publications)} downloaded={result.downloaded} "
        f"skipped={result.skipped} failed={result.failed}",
        flush=True,
    )
    return result


# ─── Orchestration ─────────────────────────────────────────────────────────

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
    path = OUT_ROOT / "_shared" / "bekendmakingen_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "generated_unix": int(time.time()),
        "source": "https://zoek.officielebekendmakingen.nl/",
        "rubrieken": sorted(TARGET_RUBRIEKEN),
        "scope_count": len(results),
        "gemeenten": [
            {
                "code": r.code,
                "slug": r.slug,
                "display": r.display,
                "publications": len(r.publications),
                "pages_seen": r.pages_seen,
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
        help="Re-download publications even if the HTML already exists.",
    )
    parser.add_argument(
        "--max-per-gemeente", type=int, default=None,
        help="Cap publications per gemeente (useful for smoke tests).",
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
        f"Bekendmakingen download — gemeenten={len(selection)} "
        f"scope={'all' if args.all_gemeenten else 'groeikernen'} "
        f"target_rubrieken={len(TARGET_RUBRIEKEN)} force={args.force} "
        f"max_per_gemeente={args.max_per_gemeente} max_pages={args.max_pages} "
        f"gemeente_concurrency={args.gemeente_concurrency}",
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
