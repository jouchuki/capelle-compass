#!/usr/bin/env python3
"""
Turn the raw Gemeenteblad (bekendmakingen) detail HTML downloaded by
``download_bekendmakingen.py`` into clean Markdown files with YAML
frontmatter — the bekendmakingen counterpart of ``clean_cvdr.py``.

Layout (per gemeente):

  data/groeikernen/{slug}/bekendmakingen/
  ├── index.json                       ← downloader writes (pub metadata)
  ├── html/gmb-YYYY-NNNNNN.html        ← downloader writes here
  └── md/gmb-YYYY-NNNNNN.md            ← cleaner writes here

Each input ``html/gmb-*.html`` produces an ``md/gmb-*.md`` containing:

  ---
  pub_id: gmb-2026-208813
  gemeente: capelle-aan-den-ijssel
  rubriek: "algemeen verbindend voorschrift (verordening)"
  title: <from index.json>
  date: <YYYY-MM-DD>
  detail_url: https://zoek.officielebekendmakingen.nl/gmb-...html
  ---

  # <title>

  <publication body, HTML stripped, paragraph-preserved>

Metadata is taken from the curated per-gemeente ``index.json`` (it already
carries rubriek/title/date/detail_url per publication); the body is lifted
from the ``#broodtekst`` container of the detail page.

Idempotent: existing ``.md`` files are skipped unless ``--force``.

Usage
-----
    python3 scripts/clean_bekendmakingen.py
    python3 scripts/clean_bekendmakingen.py --gemeente capelle-aan-den-ijssel --force
    python3 scripts/clean_bekendmakingen.py --delete-html   # drop raw HTML after clean
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from html import unescape
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = REPO_ROOT / "data" / "groeikernen"

SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.S | re.I)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
TAG_RE = re.compile(r"<[^>]+>")
BR_RE = re.compile(r"<br\s*/?>", re.I)
BLOCK_CLOSE_RE = re.compile(
    r"</(?:p|div|li|h[1-6]|blockquote|tr|section|article|ul|ol|table)\s*>",
    re.I,
)

# The publication body sits in <div id="broodtekst" ...>. Fall back to the
# main content region if that exact id is absent (older/newer page variants).
BROODTEKST_RE = re.compile(r'<div\b[^>]*\bid=(["\'])broodtekst\1[^>]*>', re.I)
CONTENT_MAIN_RE = re.compile(
    r'<div\b[^>]*\bid=(["\'])content\1[^>]*\brole=(["\'])main\2[^>]*>', re.I
)
# Where the body stops: the boilerplate disclaimer, footer link menu, or
# sidebar / related-docs chrome. KOOP pages close #content with a plain
# </div> (no </main>) and the disclaimer/footer are class-, not id-, marked —
# so match those classes, whichever appears first after the body.
BODY_END_RE = re.compile(
    r'<(?:aside|footer)\b|'
    r'<\w+\b[^>]*\b(?:id|class)=(["\'])[^"\']*'
    r'(?:disclaimer|row--footer|footer|sidebar|extrainfo|gerelateerd|'
    r'pageactions|to-top|delenModal|modal)[^"\']*\1|'
    r"</main\b|</body\b",
    re.I,
)
# The publication title is carried in frontmatter + rendered as the md H1,
# so drop the title heading from the body to avoid a duplicate. KOOP marks it
# with class "staatscourant_kop" / "single-kop-titel" — on an <h1> in some
# page variants, on a <p> in others (after a decoy empty <h1>). Match by that
# class on either tag; remove only the first (the title).
TITLE_KOP_RE = re.compile(
    r'<(h[1-6]|p)\b[^>]*\bclass=(["\'])[^"\']*'
    r'(?:staatscourant_kop|single-kop-titel)[^"\']*\2[^>]*>.*?</\1\s*>',
    re.S | re.I,
)


@dataclass
class Publication:
    pub_id: str
    gemeente: str
    rubriek: str | None
    title: str
    date: str | None
    detail_url: str | None
    body: str


def extract_body(raw_html: str) -> str:
    """Return the publication body text, paragraph-preserved.

    Anchors on ``#broodtekst`` (the KOOP publication container), falling back
    to ``#content[role=main]``. Drops nav/sidebar/footer chrome and the
    leading title H1. Returns ``""`` when no content region is found.
    """
    cleaned = SCRIPT_STYLE_RE.sub(" ", raw_html)
    cleaned = COMMENT_RE.sub(" ", cleaned)

    start_match = BROODTEKST_RE.search(cleaned) or CONTENT_MAIN_RE.search(cleaned)
    if not start_match:
        return ""
    start = start_match.end()
    end_match = BODY_END_RE.search(cleaned, start)
    fragment = cleaned[start:end_match.start()] if end_match else cleaned[start:]

    fragment = TITLE_KOP_RE.sub(" ", fragment, count=1)
    fragment = BR_RE.sub("\n", fragment)
    fragment = BLOCK_CLOSE_RE.sub("\n", fragment)
    text = TAG_RE.sub(" ", fragment)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def strip_leading_title(body: str, title: str) -> str:
    """Drop a leading body line that merely repeats the publication title.

    The title is carried in frontmatter + rendered as the md H1, so a body
    that opens by restating it is a duplicate. KOOP marks the title heading
    inconsistently across years (h1/p/span, varying classes), so we dedup on
    text, not markup: if the first non-empty line equals the title — or, for a
    sufficiently distinctive title, begins with it — that line is removed.
    """
    if not title.strip() or not body.strip():
        return body
    lines = body.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return body
    nt = _norm(title)
    nl = _norm(lines[i])

    def _drop_line() -> str:
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        return "\n".join(lines[:i] + lines[j:])

    if nl == nt:
        # Pure duplicate heading: drop the whole line.
        return _drop_line()

    if len(nt) >= 15 and nl.startswith(nt):
        # The line opens with the title but continues with real content (common
        # in short notices: "<title> / verzonden ... / het organiseren ...").
        # Strip only the title prefix + any trailing separators; keep the rest.
        prefix = re.compile(
            r"^\s*" + r"\s+".join(re.escape(w) for w in title.split())
            + r"\s*[/|>·:–—-]*\s*",
            re.I,
        )
        remainder = prefix.sub("", lines[i], count=1)
        if remainder.strip():
            lines[i] = remainder
            return "\n".join(lines)
        return _drop_line()

    return body


def _yaml_quote(value: str | None) -> str:
    """Minimal-safe YAML scalar quoting (matches clean_cvdr.py)."""
    if value is None:
        return "null"
    needs_quote = bool(re.search(r"[:#\"'\n\r]", value)) or value.strip() != value
    if needs_quote:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def render_markdown(pub: Publication) -> str:
    lines: list[str] = ["---"]
    lines.append(f"pub_id: {pub.pub_id}")
    lines.append(f"gemeente: {_yaml_quote(pub.gemeente)}")
    if pub.rubriek:
        lines.append(f"rubriek: {_yaml_quote(pub.rubriek)}")
    lines.append(f"title: {_yaml_quote(pub.title)}")
    if pub.date:
        lines.append(f"date: {pub.date}")
    if pub.detail_url:
        lines.append(f"detail_url: {pub.detail_url}")
    lines.append("---")
    lines.append("")
    if pub.title:
        lines.append(f"# {pub.title}")
        lines.append("")
    if pub.body:
        lines.append(pub.body)
    lines.append("")
    return "\n".join(lines)


def _load_index(index_path: Path) -> dict[str, dict[str, str]]:
    """pub_id -> publication metadata entry from a per-gemeente index.json."""
    if not index_path.exists():
        return {}
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {p["pub_id"]: p for p in data.get("publications", []) if p.get("pub_id")}


def _publication_for(pub_id: str, slug: str, meta: dict[str, str], body: str) -> Publication:
    title = (meta.get("title") or pub_id).strip()
    return Publication(
        pub_id=pub_id,
        gemeente=slug,
        rubriek=meta.get("rubriek"),
        title=title,
        date=meta.get("date"),
        detail_url=meta.get("detail_url"),
        body=strip_leading_title(body, title),
    )


def process_gemeente(
    slug: str,
    *,
    root: Path = OUT_ROOT,
    force: bool = False,
    delete_html: bool = False,
) -> dict[str, object]:
    """Clean every gmb-*.html for ``slug`` into md/. Returns a summary dict."""
    bk_dir = root / slug / "bekendmakingen"
    html_dir = bk_dir / "html"
    if not html_dir.is_dir():
        return {"slug": slug, "skipped": True, "reason": "html/ subdir missing"}

    md_dir = bk_dir / "md"
    md_dir.mkdir(parents=True, exist_ok=True)
    by_id = _load_index(bk_dir / "index.json")

    cleaned = 0
    skipped = 0
    failed: list[str] = []

    html_files = sorted(html_dir.glob("gmb-*.html"))
    print(f"[{slug}] cleaning {len(html_files)} bekendmakingen...", flush=True)

    for html_path in html_files:
        pub_id = html_path.stem
        md_path = md_dir / (pub_id + ".md")
        if md_path.exists() and not force:
            skipped += 1
            if delete_html:
                html_path.unlink()
            continue
        try:
            raw = html_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = html_path.read_bytes().decode("utf-8", errors="replace")
        body = extract_body(raw)
        pub = _publication_for(pub_id, slug, by_id.get(pub_id, {}), body)
        md_path.write_text(render_markdown(pub), encoding="utf-8")
        cleaned += 1
        if delete_html:
            html_path.unlink()

    summary = {
        "slug": slug,
        "cleaned": cleaned,
        "skipped": skipped,
        "failed": len(failed),
    }
    if failed:
        summary["failed_files_first5"] = failed[:5]
    print(f"[{slug}] cleaned={cleaned} skipped={skipped} failed={len(failed)}", flush=True)
    return summary


def _discover_slugs() -> list[str]:
    if not OUT_ROOT.exists():
        return []
    return sorted(
        p.name for p in OUT_ROOT.iterdir()
        if p.is_dir() and (p / "bekendmakingen" / "html").is_dir()
        and not p.name.startswith("_")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--gemeente", action="append",
        help="Slug to clean. Repeatable. Default: all gemeenten found on disk.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-clean even if the .md sidecar already exists.",
    )
    parser.add_argument(
        "--delete-html", action="store_true",
        help="Remove the raw HTML after a successful clean (saves disk).",
    )
    args = parser.parse_args()

    slugs = args.gemeente or _discover_slugs()
    if not slugs:
        print("No gemeente directories with bekendmakingen/html found.", file=sys.stderr)
        return 2

    summaries = [
        process_gemeente(slug, force=args.force, delete_html=args.delete_html)
        for slug in slugs
    ]

    manifest = OUT_ROOT / "_shared" / "bekendmakingen_clean_manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"gemeenten": summaries}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"\nclean manifest: {manifest.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
