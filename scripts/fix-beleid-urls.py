"""
Join beleid-url-catalog.json (real URLs from begrotingsapp) with
beleid-url-audit.csv (live chunk→URL mapping in Chroma) and rewrite
the policy_documents collection's metadata so every ``source_url``
either resolves (200) or is absent.

Strategy per broken URL:
  1. Extract ``(doc_type, year, doc_suffix, slug)`` from the URL path.
  2. Look up the slug in the catalog, scoped to the same (doc_type, year).
     Accept any section_type from ``{programma, paragraaf, bestuur, bestanden}``
     that exists in the catalog — the catalog is ground-truth.
  3. If a catalog URL exists and returned 200 in the catalog probe →
     rewrite the chunk's source_url to the catalog URL.
  4. Otherwise → clear the source_url (empty string). The chunk
     content stays; only the citation link disappears.

Working URLs (200) are left untouched.

Usage (from the control host):
    CAPELLE_CHROMA_HTTP=yuji-itadori:8000 \\
    /opt/backend/.venv/bin/python fix-beleid-urls.py --dry-run
    # ...review report.txt...
    CAPELLE_CHROMA_HTTP=yuji-itadori:8000 \\
    /opt/backend/.venv/bin/python fix-beleid-urls.py --execute
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import chromadb

_URL_RE = re.compile(
    r"^https://capelleaandenijssel\.begrotingsapp\.nl/"
    r"(?P<doc_type>[a-z-]+)-(?P<year>\d{4})(?P<suffix>-\d+)?/"
    r"(?P<section>[a-z-]+)/(?P<slug>[a-z0-9-]+)$"
)


class URLFixer:
    """Joins catalog + audit to produce metadata patches for Chroma."""

    def __init__(self, catalog_path: Path, audit_path: Path) -> None:
        self._catalog = self._load_catalog(catalog_path)
        self._audit = self._load_audit(audit_path)

    @staticmethod
    def _load_catalog(path: Path) -> dict[tuple[str, str], list[dict]]:
        """Index catalog by (doc_key, slug) for O(1) lookup.

        doc_key is ``"doc_type-year(-suffix)?"`` matching the path
        segment. Multiple section_types may share a slug across docs;
        we keep every candidate and prefer 200-OK ones at resolve time.
        """
        data = json.loads(path.read_text())
        by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for row in data["urls"]:
            m = _URL_RE.match(row["url"])
            if not m:
                continue
            doc_key = f"{m.group('doc_type')}-{m.group('year')}"
            if m.group("suffix"):
                doc_key += m.group("suffix")
            by_key[(doc_key, m.group("slug"))].append(
                {
                    "url": row["url"],
                    "section": m.group("section"),
                    "http_status": row.get("http_status"),
                }
            )
        return by_key

    @staticmethod
    def _load_audit(path: Path) -> list[dict]:
        """Audit rows already carry status + chunks_affected."""
        with path.open() as fp:
            return list(csv.DictReader(fp))

    def resolve(self, broken_url: str) -> str | None:
        """Return a replacement URL or None if none exists.

        The slug from the broken URL is the join key. The catalog
        may have the same slug under a different section (e.g.
        ``/paragraaf/paragraaf-weerstandsvermogen-en-risicobeheersing``
        is 404, but ``/programma/paragraaf-weerstandsvermogen-en-risicobeheersing``
        is 200). Prefer 200-OK candidates.
        """
        m = _URL_RE.match(broken_url)
        if not m:
            return None
        doc_key = f"{m.group('doc_type')}-{m.group('year')}"
        if m.group("suffix"):
            doc_key += m.group("suffix")
        slug = m.group("slug")
        candidates = self._catalog.get((doc_key, slug), [])
        if not candidates:
            # The slug might have been rewritten — paragraaf docs
            # often surface as ``programma/paragraaf-<slug>``.
            if not slug.startswith("paragraaf-"):
                candidates = self._catalog.get(
                    (doc_key, f"paragraaf-{slug}"), []
                )
        good = [c for c in candidates if c.get("http_status") == 200]
        if good:
            return good[0]["url"]
        return None

    def build_plan(self) -> dict:
        """Classify every audit row into an action bucket."""
        plan = {
            "keep_200": [],          # no action
            "rewrite_to_programma": [],  # map to new /programma URL
            "rewrite_cross_section": [],  # map across section_type
            "strip_unresolvable": [],  # no catalog match → clear
            "strip_non_200_catalog": [],  # catalog knows it but also 404
        }
        for row in self._audit:
            status = row["status"]
            url = row["url"]
            if status == "200":
                plan["keep_200"].append(row)
                continue
            replacement = self.resolve(url)
            if replacement and replacement != url:
                m = _URL_RE.match(url)
                mrepl = _URL_RE.match(replacement)
                if m and mrepl and m.group("section") != mrepl.group("section"):
                    plan["rewrite_cross_section"].append({**row, "new_url": replacement})
                else:
                    plan["rewrite_to_programma"].append({**row, "new_url": replacement})
            else:
                plan["strip_unresolvable"].append(row)
        return plan


class ChromaPatcher:
    """Apply (url_old → url_new, url_old → '') rewrites to metadata."""

    def __init__(self, host: str, port: int, collection: str) -> None:
        self._client = chromadb.HttpClient(host=host, port=port)
        self._col = self._client.get_collection(collection)

    def apply(
        self,
        rewrites: dict[str, str],
        strips: set[str],
        *,
        dry_run: bool,
    ) -> dict[str, int]:
        """Fetch every matching chunk and call update() per batch.

        Chroma's metadata filter uses ``$in`` for URL lists.
        """
        counts = Counter()
        affected_urls = set(rewrites) | strips
        if not affected_urls:
            return dict(counts)

        batch_size = 500
        # Iterate URLs rather than one giant $in — keeps per-request latency bounded.
        for old_url in affected_urls:
            result = self._col.get(
                where={"source_url": old_url},
                include=["metadatas"],
                limit=10000,
            )
            ids = result.get("ids") or []
            metadatas = result.get("metadatas") or []
            if not ids:
                continue

            new_metas = []
            for meta in metadatas:
                patched = dict(meta)
                if old_url in rewrites:
                    patched["source_url"] = rewrites[old_url]
                    counts["rewritten_chunks"] += 1
                else:
                    patched["source_url"] = ""
                    counts["stripped_chunks"] += 1
                new_metas.append(patched)

            if dry_run:
                continue

            for i in range(0, len(ids), batch_size):
                self._col.update(
                    ids=ids[i : i + batch_size],
                    metadatas=new_metas[i : i + batch_size],
                )
        counts["urls_touched"] = len(affected_urls)
        return dict(counts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="deploy/beleid-url-catalog.json")
    ap.add_argument("--audit", default="deploy/beleid-url-audit.csv")
    ap.add_argument("--collection", default="policy_documents")
    ap.add_argument("--chroma-http", default=os.environ.get("CAPELLE_CHROMA_HTTP", ""))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--report", default="deploy/beleid-url-fix-report.txt")
    args = ap.parse_args()

    if not (args.dry_run ^ args.execute):
        print("Pick exactly one of --dry-run / --execute", file=sys.stderr)
        return 2

    if not args.chroma_http:
        print("CAPELLE_CHROMA_HTTP or --chroma-http required", file=sys.stderr)
        return 2
    host, _, port_s = args.chroma_http.partition(":")
    port = int(port_s or "8000")

    fixer = URLFixer(Path(args.catalog), Path(args.audit))
    plan = fixer.build_plan()

    rewrites: dict[str, str] = {}
    strips: set[str] = set()
    for row in plan["rewrite_to_programma"] + plan["rewrite_cross_section"]:
        rewrites[row["url"]] = row["new_url"]
    for row in plan["strip_unresolvable"] + plan["strip_non_200_catalog"]:
        strips.add(row["url"])

    Path(args.report).write_text(_format_report(plan, rewrites, strips))
    print(f"Wrote {args.report}")
    for bucket, rows in plan.items():
        print(f"  {bucket:<28} {len(rows):>5} URLs")

    patcher = ChromaPatcher(host, port, args.collection)
    counts = patcher.apply(rewrites, strips, dry_run=args.dry_run)
    print(f"{'DRY-RUN' if args.dry_run else 'APPLIED'}: {counts}")
    return 0


def _format_report(plan: dict, rewrites: dict[str, str], strips: set[str]) -> str:
    lines = ["Beleid URL fix report", "=" * 60, ""]
    for bucket, rows in plan.items():
        lines.append(f"[{bucket}] {len(rows)}")
        for r in rows[:10]:
            if "new_url" in r:
                lines.append(f"  {r['url']}\n    → {r['new_url']}  ({r.get('chunks_affected','?')} chunks)")
            else:
                lines.append(f"  {r['url']}  ({r.get('chunks_affected','?')} chunks)")
        if len(rows) > 10:
            lines.append(f"  ... and {len(rows) - 10} more")
        lines.append("")
    lines.append(f"Total rewrites: {len(rewrites)}")
    lines.append(f"Total strips:   {len(strips)}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
