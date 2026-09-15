#!/usr/bin/env python3
"""
Download Open Spending Iv3 data for the 7 groeikernen (Capelle + 6 peer
new-towns). Stores everything as local files under
``data/groeikernen/`` — no DB, no embeddings, just a corpus.

For each ``(gemeente, year)`` we pull:
  - the Fiscal Data Package descriptor (JSON)
  - the per-gemeente CSV (lasten + baten per categorie × post × verslagsoort)
  - the year's dimension CSVs once (shared across gemeenten)

The script is idempotent: re-running skips files that already exist on
disk unless ``--force`` is passed. Concurrent fetches are capped via a
small semaphore so we stay polite to openspending.nl + data2.openspending.nl.

Output layout
-------------
data/groeikernen/
├── capelle-aan-den-ijssel/iv3/2026/descriptor.json
│                              /2026/data.csv
│                              /2025/...
├── almere/iv3/2026/...
├── ...
└── _shared/iv3/2026/{categorie,categorygroup,post,verslagsoort,source}.csv
└── _shared/iv3/manifest.json   # per-run trace, what was fetched/skipped/failed

Usage
-----
    python3 scripts/download_openspending.py            # incremental
    python3 scripts/download_openspending.py --force    # re-download all
    python3 scripts/download_openspending.py --years 2024 2025 2026
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    # Ensure the sibling CVDR downloader is importable regardless of how this
    # module is loaded (CLI, ``-m``, or ``spec_from_file_location`` in tests).
    sys.path.insert(0, str(_SCRIPTS_DIR))

from download_cvdr import _load_all_gemeenten  # noqa: E402  (sibling import after path setup)

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = REPO_ROOT / "data" / "groeikernen"

# Open Spending hosts FDPs at openspending.nl and the bulk CSVs on a CDN
# at data2.openspending.nl. Both are used.
FDP_BASE = "https://openspending.nl/fiscaldatapackage/Gemeenten/{year}/{slug}"

# Open Spending's per-gemeente slug differs from our canonical slug for a
# handful of municipalities: province-name collisions get a ``-gemeente``
# suffix, namesakes get a province abbreviation (``-l``/``-nh``/``-o``/``-z``),
# Frisian municipalities use their Frisian name, and Den Haag is published
# as ``s-gravenhage-gemeente``. We fetch via the OS slug but still store
# under our slug. Discovered empirically against data2.openspending.nl.
SLUG_OVERRIDES: dict[str, str] = {
    "den-haag": "s-gravenhage-gemeente",
    "groningen": "groningen-gemeente",
    "utrecht": "utrecht-gemeente",
    "bergen-limburg": "bergen-l",
    "bergen-noord-holland": "bergen-nh",
    "laren": "laren-nh",
    "beek": "beek-l",
    "stein": "stein-l",
    "hengelo": "hengelo-o",
    "middelburg": "middelburg-z",
    "rijswijk": "rijswijk-zh",
    "dantumadeel": "dantumadiel",
    "de-friese-meren": "de-fryske-marren",
    "tietjerksteradeel": "tytsjerksteradiel",
    "nuenen-c-a": "nuenen-gerwen-en-nederwetten",
}
DIMENSIONS = (
    "categorie",
    "categorygroup",
    "post",
    "verslagsoort",
    "source",
)

# The 7 groeikernen we comparison-track. Slug = Open Spending URL slug;
# Code = CBS gemeentecode (anchored to incijfers_domain_probe + CBS lookup).
GROEIKERNEN: tuple[tuple[str, str], ...] = (
    ("GM0502", "capelle-aan-den-ijssel"),
    ("GM0034", "almere"),
    ("GM0637", "zoetermeer"),
    ("GM0356", "nieuwegein"),
    ("GM0439", "purmerend"),
    ("GM0995", "lelystad"),
    ("GM0321", "houten"),
)

DEFAULT_YEARS = tuple(range(2020, 2027))  # inclusive

USER_AGENT = "datakompas-corpus/0.1 (+https://data-compass.org)"
REQUEST_TIMEOUT = 30.0
CONCURRENCY = 4  # gentle on the host


def _select_gemeenten(all_gemeenten: bool) -> list[tuple[str, str]]:
    """Resolve which ``(GMcode, slug)`` pairs to download.

    ``all_gemeenten=False`` (default): the 7 tracked groeikernen.
    ``all_gemeenten=True``: every regular Dutch municipality (>= 342),
    sourced from ``download_cvdr._load_all_gemeenten`` so the Iv3 and CVDR
    corpora share an identical slug scheme and land under the same
    ``data/groeikernen/<slug>/`` tree. The CVDR loader returns
    ``(code, slug, display)`` triples; the Iv3 download only needs
    ``(code, slug)``, so the display name is dropped. No network access.
    """
    if all_gemeenten:
        return [(code, slug) for code, slug, _display in _load_all_gemeenten()]
    return list(GROEIKERNEN)


@dataclass(frozen=True)
class FetchResult:
    """One row in the run manifest."""

    target: str
    url: str
    status: str  # "ok" | "skipped" | "failed"
    bytes: int
    detail: str = ""


def _fetch(url: str, dest: Path) -> FetchResult:
    """Blocking HTTP GET → file write. Used inside the asyncio thread pool."""
    headers = {"User-Agent": USER_AGENT}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            payload = resp.read()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        return FetchResult(target=str(dest.relative_to(REPO_ROOT)), url=url,
                           status="ok", bytes=len(payload))
    except HTTPError as exc:
        return FetchResult(target=str(dest.relative_to(REPO_ROOT)), url=url,
                           status="failed", bytes=0,
                           detail=f"HTTP {exc.code} {exc.reason}")
    except (URLError, TimeoutError) as exc:
        return FetchResult(target=str(dest.relative_to(REPO_ROOT)), url=url,
                           status="failed", bytes=0,
                           detail=f"{type(exc).__name__}: {exc}")


async def _fetch_async(sem: asyncio.Semaphore, url: str, dest: Path,
                       *, force: bool) -> FetchResult:
    """Async wrapper that respects --force + the concurrency semaphore."""
    if dest.exists() and not force:
        return FetchResult(target=str(dest.relative_to(REPO_ROOT)), url=url,
                           status="skipped", bytes=dest.stat().st_size)
    async with sem:
        return await asyncio.to_thread(_fetch, url, dest)


async def _download_year(year: int, sem: asyncio.Semaphore,
                         gemeenten: list[tuple[str, str]],
                         *, force: bool) -> list[FetchResult]:
    """Fetch the year's dimension files + every gemeente's FDP + data CSV."""
    results: list[FetchResult] = []
    shared_root = OUT_ROOT / "_shared" / "iv3" / str(year)
    dim_tasks = [
        _fetch_async(
            sem,
            f"https://data2.openspending.nl/fiscaldatapackage/Gemeenten/{year}/{name}.csv",
            shared_root / f"{name}.csv",
            force=force,
        )
        for name in DIMENSIONS
    ]
    results.extend(await asyncio.gather(*dim_tasks))

    per_gemeente_tasks: list[asyncio.Task[FetchResult]] = []
    for _, slug in gemeenten:
        # Write under OUR canonical slug (so the agent's paths are stable),
        # but fetch using Open Spending's slug, which differs for ~15
        # namesake / Frisian / province-collision municipalities.
        os_slug = SLUG_OVERRIDES.get(slug, slug)
        gem_root = OUT_ROOT / slug / "iv3" / str(year)
        per_gemeente_tasks.append(asyncio.create_task(_fetch_async(
            sem,
            FDP_BASE.format(year=year, slug=os_slug),
            gem_root / "descriptor.json",
            force=force,
        )))
        per_gemeente_tasks.append(asyncio.create_task(_fetch_async(
            sem,
            f"https://data2.openspending.nl/fiscaldatapackage/Gemeenten/{year}/{os_slug}.csv",
            gem_root / "data.csv",
            force=force,
        )))
    results.extend(await asyncio.gather(*per_gemeente_tasks))
    return results


async def _run(years: tuple[int, ...], gemeenten: list[tuple[str, str]],
               *, force: bool) -> list[FetchResult]:
    sem = asyncio.Semaphore(CONCURRENCY)
    out: list[FetchResult] = []
    for year in years:
        print(f"[year={year}] fetching dimensions + per-gemeente data...", flush=True)
        year_results = await _download_year(year, sem, gemeenten, force=force)
        ok = sum(1 for r in year_results if r.status == "ok")
        skipped = sum(1 for r in year_results if r.status == "skipped")
        failed = sum(1 for r in year_results if r.status == "failed")
        bytes_new = sum(r.bytes for r in year_results if r.status == "ok")
        print(
            f"[year={year}] ok={ok} skipped={skipped} failed={failed} "
            f"bytes_new={bytes_new}",
            flush=True,
        )
        out.extend(year_results)
    return out


def _write_manifest(results: list[FetchResult], years: tuple[int, ...],
                    gemeenten: list[tuple[str, str]]) -> Path:
    """Drop a manifest at data/groeikernen/_shared/iv3/manifest.json."""
    manifest_path = OUT_ROOT / "_shared" / "iv3" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    summary: dict[str, object] = {
        "generated_unix": int(time.time()),
        "years": list(years),
        "gemeenten": [{"code": code, "slug": slug} for code, slug in gemeenten],
        "counts": {
            "ok": sum(1 for r in results if r.status == "ok"),
            "skipped": sum(1 for r in results if r.status == "skipped"),
            "failed": sum(1 for r in results if r.status == "failed"),
        },
        "failures": [
            {"target": r.target, "url": r.url, "detail": r.detail}
            for r in results if r.status == "failed"
        ],
    }
    manifest_path.write_text(json.dumps(summary, indent=2) + "\n")
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--years", type=int, nargs="+", default=list(DEFAULT_YEARS),
                        help="Which calendar years to fetch (default: 2020..2026)")
    parser.add_argument("--force", action="store_true",
                        help="Re-download files even if they already exist on disk")
    parser.add_argument(
        "--all-gemeenten", action="store_true",
        help="Fetch every regular Dutch municipality (national scope) instead "
             "of just the 7 groeikernen.",
    )
    args = parser.parse_args()
    years = tuple(args.years)
    gemeenten = _select_gemeenten(all_gemeenten=args.all_gemeenten)

    print(
        f"Open Spending download — gemeenten={len(gemeenten)} years={list(years)} "
        f"force={args.force} all_gemeenten={args.all_gemeenten}",
        flush=True,
    )
    results = asyncio.run(_run(years, gemeenten, force=args.force))
    manifest = _write_manifest(results, years, gemeenten)
    failed = [r for r in results if r.status == "failed"]
    print(f"\nmanifest: {manifest.relative_to(REPO_ROOT)}")
    if failed:
        print(f"FAILED targets ({len(failed)}):")
        for r in failed[:20]:
            print(f"  - {r.target}: {r.detail}")
        return 1
    print("DONE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
