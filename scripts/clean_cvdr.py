#!/usr/bin/env python3
"""
Turn the raw CVDR detail HTML downloaded by ``download_cvdr.py`` into
clean Markdown files with YAML frontmatter.

Layout (per gemeente):

  data/groeikernen/{slug}/verordeningen/
  ├── index.json
  ├── html/CVDR{id}_v{ver}.html   ← downloader writes here
  └── md/CVDR{id}_v{ver}.md       ← cleaner writes here

Each input ``html/CVDR{id}_v{ver}.html`` produces an ``md/CVDR{id}_v{ver}.md``
containing:

  ---
  cvdr_id: 100005
  version: 1
  title: <DCTERMS.title>
  short_title: <DCTERMS.alternative>
  type: beleidsregel | verordening | aanwijzingsbesluit | mandaatbesluit
        | reglement | nadere_regel | besluit | regeling | overig
  gemeente: <DCTERMS.creator>
  modified: <DCTERMS.modified — YYYY-MM-DD>
  detail_url: https://lokaleregelgeving.overheid.nl/CVDR{id}/{ver}
  ---

  # <title>

  <regeling body, HTML stripped, paragraph-preserved>

Per-gemeente ``verordeningen/index.json`` is enriched with the ``type``
field per regeling and a ``type_counts`` aggregate so the indexer can
filter without re-parsing the markdown.

Idempotent: existing ``.md`` files are skipped unless ``--force``.

Usage
-----
    python3 scripts/clean_cvdr.py
    python3 scripts/clean_cvdr.py --gemeente capelle-aan-den-ijssel --force
    python3 scripts/clean_cvdr.py --delete-html   # drop raw HTML after clean
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = REPO_ROOT / "data" / "groeikernen"

# Gemeente-slug → directory under data/groeikernen/. Discovered at runtime
# from the on-disk corpus, no hard-coded list needed.

# DCTERMS meta fields we lift from the page <head>.
META_FIELDS: tuple[tuple[str, str], ...] = (
    ("DCTERMS.identifier", "identifier"),
    ("DCTERMS.creator",    "creator"),
    ("DCTERMS.title",      "title"),
    ("DCTERMS.alternative", "alternative"),
    ("DCTERMS.modified",   "modified"),
    ("DCTERMS.language",   "language"),
)

META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I)
ATTR_RE = re.compile(
    r"([:\w.-]+)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)",
    re.S,
)
SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.S | re.I)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
TAG_RE = re.compile(r"<[^>]+>")
REGELING_TEKST_RE = re.compile(
    r"<(?P<tag>[a-z0-9]+)\b[^>]*class=(?P<q>['\"])[^'\"]*regeling-tekst[^'\"]*(?P=q)[^>]*>",
    re.I,
)
BODY_END_RE = re.compile(
    r'<div\b[^>]*class=(["\'])[^"\']*(regeling-metadata|metadata|regeling-info)[^"\']*\1|'
    r"</main\b|<footer\b|</body\b",
    re.I,
)
BLOCK_CLOSE_RE = re.compile(
    r"</(?:p|div|li|h[1-6]|blockquote|tr|section|article|ul|ol|table)\s*>",
    re.I,
)
BR_RE = re.compile(r"<br\s*/?>", re.I)

# Title → regeling-type classifier. Order matters; first match wins.
# Each entry: (compiled regex, type slug). The regex runs against the
# normalized title (trimmed + collapsed whitespace, lowercased).
#
# Strategy: PREFIX match for distinctive types ("beleidsregel", "leidraad",
# "gedragscode" — these never appear inside compound nouns); SUFFIX match
# for the four big Dutch regeling instruments ("...verordening",
# "...reglement", "...regeling", "...besluit") so we catch compounds
# like "Re-integratieverordening", "Subsidieregeling", "Marktreglement",
# "Instellingsbesluit".
TYPE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Prefix matches — high-signal words that lead the title.
    (re.compile(r"^beleidsregel(s)?\b"),               "beleidsregel"),
    (re.compile(r"^(sub-?)?mandaat(besluit|regeling)?\b"), "mandaatbesluit"),
    (re.compile(r"^aanwijzing(s|sbesluit)?\b"),        "aanwijzingsbesluit"),
    (re.compile(r"^instellings(besluit|regeling)\b"),  "instellingsbesluit"),
    (re.compile(r"^leidraad\b"),                       "leidraad"),
    (re.compile(r"^gedragscode\b"),                    "gedragscode"),
    (re.compile(r"^nota\b"),                           "nota"),
    (re.compile(r"^handelingskader\b|^\w*kader\b"),    "kader"),
    (re.compile(r"^grondprijzenbrief\b"),              "grondprijzenbrief"),
    (re.compile(r"^klachten(regeling|reglement)\b"),   "klachtenregeling"),
    (re.compile(r"^privacy(regeling|reglement)\b"),    "privacyregeling"),
    (re.compile(r"^nadere\s+regel(s|ing)?\b"),         "nadere_regel"),
    # Suffix matches — catch compound nouns ending in the four instrument
    # families. Use \w* not .* so "Verordening van de raad" still hits
    # the prefix path first via the suffix.
    (re.compile(r"verordening\b"),                     "verordening"),
    (re.compile(r"reglement\b"),                       "reglement"),
    (re.compile(r"besluit\b"),                         "besluit"),
    (re.compile(r"regeling\b"),                        "regeling"),
    # Anywhere — fallback for unusual titles.
    (re.compile(r"\bbeleidsregel(s)?\b"),              "beleidsregel"),
)


@dataclass
class CleanedRegeling:
    cvdr_id: str
    version: str
    title: str
    short_title: str | None
    regeling_type: str
    gemeente: str
    modified: str | None
    detail_url: str
    body: str


def _classify(title: str) -> str:
    """Pick a type slug from the regeling title. Falls back to 'overig'.

    Title is lowercased + whitespace-normalised before matching so the
    classifier doesn't have to carry an ``re.I`` flag on every pattern
    (and so we can use ``\\b`` reliably).
    """
    norm = re.sub(r"\s+", " ", title.strip()).lower()
    for pattern, slug in TYPE_PATTERNS:
        if pattern.search(norm):
            return slug
    return "overig"


def _attrs(tag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, raw_value in ATTR_RE.findall(tag):
        value = raw_value.strip()
        if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
            value = value[1:-1]
        out[key.lower()] = unescape(value)
    return out


def _extract_meta(raw_html: str) -> dict[str, str]:
    """Lift DCTERMS meta tags out of <head> into a flat dict."""
    out: dict[str, str] = {}
    for meta in META_TAG_RE.findall(raw_html):
        attrs = _attrs(meta)
        name = attrs.get("name") or ""
        content = attrs.get("content") or ""
        if not name.startswith("DCTERMS."):
            continue
        for full, key in META_FIELDS:
            if name == full:
                out[key] = content
                break
    return out


def _extract_body(raw_html: str) -> str:
    """
    Find the regeling-tekst container and return its text content,
    paragraph-preserved. Drops navigation, footers, accessibility links,
    Piwik tags, etc. — none of those live inside .regeling-tekst.

    If no .regeling-tekst is present (rare), falls back to <main> or <article>;
    if even those are missing, returns "".
    """
    cleaned = SCRIPT_STYLE_RE.sub(" ", raw_html)
    cleaned = COMMENT_RE.sub(" ", cleaned)

    start_match = REGELING_TEKST_RE.search(cleaned)
    if start_match:
        start = start_match.end()
        end_match = BODY_END_RE.search(cleaned, start)
        fragment = cleaned[start:end_match.start()] if end_match else cleaned[start:]
    else:
        fallback = re.search(
            r"<main\b[^>]*>(?P<body>.*?)</main\s*>",
            cleaned,
            re.S | re.I,
        )
        if fallback is None:
            fallback = re.search(
                r"<article\b[^>]*>(?P<body>.*?)</article\s*>",
                cleaned,
                re.S | re.I,
            )
        if fallback is None:
            return ""
        fragment = fallback.group("body")

    fragment = BR_RE.sub("\n", fragment)
    fragment = BLOCK_CLOSE_RE.sub("\n", fragment)
    text = TAG_RE.sub(" ", fragment)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _detail_url(cvdr_id: str, version: str) -> str:
    return f"https://lokaleregelgeving.overheid.nl/CVDR{cvdr_id}/{version}"


def _clean_file(html_path: Path) -> CleanedRegeling | None:
    match = re.match(r"CVDR(\d+)_v(\d+)\.html$", html_path.name)
    if not match:
        return None
    cvdr_id, version = match.group(1), match.group(2)

    try:
        raw = html_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = html_path.read_bytes().decode("utf-8", errors="replace")

    meta = _extract_meta(raw)
    title = (meta.get("title") or "").strip()
    body = _extract_body(raw)

    return CleanedRegeling(
        cvdr_id=cvdr_id,
        version=version,
        title=title,
        short_title=(meta.get("alternative") or None),
        regeling_type=_classify(title) if title else "overig",
        gemeente=meta.get("creator") or "",
        modified=meta.get("modified") or None,
        detail_url=_detail_url(cvdr_id, version),
        body=body,
    )


def _yaml_quote(value: str) -> str:
    """Minimal-safe YAML scalar quoting."""
    if value is None:
        return "null"
    needs_quote = bool(re.search(r"[:#\"'\n\r]", value)) or value.strip() != value
    if needs_quote:
        # Use double quotes, escape backslashes + double quotes inside.
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _render_markdown(r: CleanedRegeling) -> str:
    lines: list[str] = ["---"]
    lines.append(f"cvdr_id: {r.cvdr_id}")
    lines.append(f"version: {r.version}")
    lines.append(f"title: {_yaml_quote(r.title)}")
    if r.short_title and r.short_title != r.title:
        lines.append(f"short_title: {_yaml_quote(r.short_title)}")
    lines.append(f"type: {r.regeling_type}")
    lines.append(f"gemeente: {_yaml_quote(r.gemeente)}")
    if r.modified:
        lines.append(f"modified: {r.modified}")
    lines.append(f"detail_url: {r.detail_url}")
    lines.append("---")
    lines.append("")
    if r.title:
        lines.append(f"# {r.title}")
        lines.append("")
    if r.body:
        lines.append(r.body)
    lines.append("")
    return "\n".join(lines)


def _process_gemeente(slug: str, *, force: bool, delete_html: bool) -> dict[str, object]:
    gem_dir = OUT_ROOT / slug / "verordeningen"
    if not gem_dir.exists():
        return {"slug": slug, "skipped": True, "reason": "directory missing"}

    html_dir = gem_dir / "html"
    md_dir = gem_dir / "md"
    if not html_dir.exists():
        return {"slug": slug, "skipped": True, "reason": "html/ subdir missing"}
    md_dir.mkdir(parents=True, exist_ok=True)

    counters: Counter[str] = Counter()
    cleaned = 0
    skipped = 0
    failed: list[str] = []
    type_by_id: dict[str, str] = {}

    html_files = sorted(html_dir.glob("CVDR*.html"))
    print(f"[{slug}] cleaning {len(html_files)} regelingen...", flush=True)

    for html_path in html_files:
        md_path = md_dir / (html_path.stem + ".md")
        if md_path.exists() and not force:
            # Still need the type for the index — re-parse cheaply from MD.
            existing = _read_type_from_md(md_path)
            if existing:
                counters[existing] += 1
                type_by_id[html_path.stem] = existing
            skipped += 1
            if delete_html and html_path.exists():
                html_path.unlink()
            continue
        parsed = _clean_file(html_path)
        if parsed is None or not parsed.title:
            failed.append(html_path.name)
            continue
        md_path.write_text(_render_markdown(parsed), encoding="utf-8")
        counters[parsed.regeling_type] += 1
        type_by_id[html_path.stem] = parsed.regeling_type
        cleaned += 1
        if delete_html:
            html_path.unlink()

    # Enrich the index.json — add type per entry + type_counts aggregate.
    index_path = gem_dir / "index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        for entry in index.get("regelingen", []):
            stem = f"CVDR{entry['cvdr_id']}_v{entry['version']}"
            entry["type"] = type_by_id.get(stem, entry.get("type", "overig"))
        index["type_counts"] = dict(sorted(counters.items()))
        index_path.write_text(
            json.dumps(index, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    summary = {
        "slug": slug,
        "cleaned": cleaned,
        "skipped": skipped,
        "failed": len(failed),
        "type_counts": dict(sorted(counters.items())),
    }
    if failed:
        summary["failed_files_first5"] = failed[:5]
    print(
        f"[{slug}] cleaned={cleaned} skipped={skipped} failed={len(failed)} "
        f"types={dict(sorted(counters.items()))}",
        flush=True,
    )
    return summary


def _read_type_from_md(md_path: Path) -> str | None:
    """Cheap frontmatter sniff so we don't re-parse skipped files fully."""
    try:
        head = md_path.read_text(encoding="utf-8")[:600]
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r"^type:\s*([a-z_]+)\s*$", head, re.M)
    return m.group(1) if m else None


def _discover_slugs() -> list[str]:
    if not OUT_ROOT.exists():
        return []
    return sorted(
        p.name for p in OUT_ROOT.iterdir()
        if p.is_dir() and (p / "verordeningen").exists() and not p.name.startswith("_")
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
        print("No gemeente directories found.", file=sys.stderr)
        return 2

    summaries = [
        _process_gemeente(slug, force=args.force, delete_html=args.delete_html)
        for slug in slugs
    ]

    manifest = OUT_ROOT / "_shared" / "cvdr_clean_manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"gemeenten": summaries}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"\nclean manifest: {manifest.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
