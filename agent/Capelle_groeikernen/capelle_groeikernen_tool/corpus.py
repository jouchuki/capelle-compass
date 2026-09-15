"""
Corpus discovery + text-extraction helpers.

The corpus lives under ``$GROEIKERNEN_DATA`` (default: the path inside
``compass/data/groeikernen``). It has two text-bearing sources we index:

  * ``{slug}/verordeningen/md/CVDR<id>_v<v>.md``
      Clean Markdown of currently-in-force CVDR regelingen, with YAML
      frontmatter (title, type, modified). One file per regeling, one
      version per file.

  * ``{slug}/bekendmakingen/md/gmb-YYYY-NNNNNN.md``
      Clean Markdown of Gemeenteblad publication pages (verordeningen,
      beleidsregels, ander besluit van algemene strekking, delegatie- of
      mandaatbesluit), with YAML frontmatter (date, rubriek, title). One
      file per publication. Only Markdown is indexed — raw HTML is never
      read (the national corpus ships md only).

The two sources overlap deliberately: CVDR carries the consolidated
text, bekendmakingen carries the version history. Search hits both.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal


DEFAULT_GEMEENTEN: tuple[tuple[str, str], ...] = (
    ("capelle-aan-den-ijssel", "Capelle aan den IJssel"),
    ("almere",                 "Almere"),
    ("zoetermeer",             "Zoetermeer"),
    ("nieuwegein",             "Nieuwegein"),
    ("purmerend",              "Purmerend"),
    ("lelystad",               "Lelystad"),
    ("houten",                 "Houten"),
)
GEMEENTEN = DEFAULT_GEMEENTEN
GEMEENTE_SLUGS: frozenset[str] = frozenset(s for s, _ in DEFAULT_GEMEENTEN)

SourceKind = Literal["cvdr", "bekendmakingen"]
SOURCE_KINDS: tuple[SourceKind, ...] = ("cvdr", "bekendmakingen")

# The bundled workspace links data/ to Compass's repository data directory.
# GROEIKERNEN_DATA remains the explicit override for provisioned corpora.
_DATA_ROOT_CANDIDATES: tuple[str, ...] = (
    str(Path(__file__).resolve().parents[2] / "data" / "groeikernen"),
)

# HTML→text stripping. Keep it surgical: drop <script>/<style> bodies first,
# then drop tags, then collapse whitespace. Not a full HTML parser — good
# enough for search-context extraction.
_SCRIPT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.S)


def data_root() -> Path:
    """Resolve the corpus root.

    Priority: ``$GROEIKERNEN_DATA`` wins; otherwise the first existing
    candidate in :data:`_DATA_ROOT_CANDIDATES`; otherwise the last
    candidate (so ``coverage`` emits zero counts instead of crashing
    when the corpus genuinely isn't on disk).
    """
    env = os.environ.get("GROEIKERNEN_DATA", "").strip()
    if env:
        return Path(env)
    for candidate in _DATA_ROOT_CANDIDATES:
        p = Path(candidate)
        if p.is_dir():
            return p
    return Path(_DATA_ROOT_CANDIDATES[-1])


@dataclass(frozen=True)
class Document:
    """One indexable text document in the corpus."""

    gemeente: str        # slug (e.g. "capelle-aan-den-ijssel")
    source: SourceKind   # "cvdr" | "bekendmakingen"
    doc_id: str          # "CVDR337993_v1" or "gmb-2026-224348"
    path: Path
    rubriek: str | None = None   # bekendmakingen: e.g. "beleidsregel"
    cvdr_type: str | None = None  # cvdr: e.g. "verordening" (from frontmatter)
    title: str | None = None
    date: str | None = None       # ISO yyyy-mm-dd if known


# ─── HTML / Markdown → search text ─────────────────────────────────────────

def strip_html(html: str) -> str:
    """HTML → plain text. Drops <script>/<style>, collapses whitespace."""
    html = _SCRIPT_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", html)
    # Decode the handful of entities that actually show up in KOOP pages.
    text = (text.replace("&nbsp;", " ")
                .replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&quot;", '"')
                .replace("&#xEB;", "ë")
                .replace("&#xE9;", "é")
                .replace("&#xEF;", "ï"))
    return _WS_RE.sub(" ", text).strip()


def strip_frontmatter(md: str) -> str:
    """Drop the YAML frontmatter block of a cleaned-CVDR Markdown file."""
    return _FRONTMATTER_RE.sub("", md, count=1)


def _cache_root() -> Path:
    """Resolve the on-disk text cache root.

    Priority:

    1. ``$GROEIKERNEN_CACHE`` — explicit override.
    2. ``$XDG_CACHE_HOME/groeikernen/v1`` — honour an explicit XDG cache.
    3. ``<data_root>/../groeikernen-cache/v1`` — co-located with the corpus.
    4. ``~/.cache/groeikernen/v1`` — last resort (corpus not on disk).

    The cache key is the source file path + mtime; if either differs from the
    cached version's metadata, the cached file is invalidated.

    **Why the default is co-located with the corpus, not under ``~/.cache``:**
    the stripped-text cache is keyed only by document path + mtime — NOT by
    user. A per-``$HOME`` default means a cache built under one HOME (e.g. the
    deploy running as root) is invisible to a caller running under a different
    HOME (e.g. a sandboxed agent shell). The rg-backed search path then greps
    an empty directory and returns ZERO hits with no error — a silent, total
    data-integrity failure. Co-locating the cache with the (world-readable)
    corpus makes it reachable by any process that can read the corpus at all.
    """
    raw = os.environ.get("GROEIKERNEN_CACHE", "").strip()
    if raw:
        return Path(raw)
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg:
        return Path(xdg) / "groeikernen" / "v1"
    root = data_root()
    if root.is_dir():
        return root.parent / "groeikernen-cache" / "v1"
    return Path.home() / ".cache" / "groeikernen" / "v1"


def _cache_path_for(doc: Document) -> Path:
    return _cache_root() / doc.gemeente / doc.source / f"{doc.doc_id}.txt"


def _strip_for(doc: Document, raw: str) -> str:
    # Cleaned Markdown (cvdr, and now bekendmakingen) carries YAML frontmatter;
    # legacy bekendmakingen are raw HTML. Key on the file type, not the source,
    # so a bekendmakingen md is stripped of frontmatter — not run through the
    # HTML stripper, which would leave the frontmatter keys in the body.
    return strip_frontmatter(raw) if doc.path.suffix == ".md" else strip_html(raw)


def load_text(doc: Document) -> str:
    """Return the searchable text body of a document.

    On first call for a doc, the stripped text is written to disk under
    the cache root and re-used by subsequent queries. The cache is
    invalidated automatically when the source file's mtime changes.
    """
    cache_path = _cache_path_for(doc)
    try:
        src_mtime = doc.path.stat().st_mtime_ns
        if cache_path.exists() and cache_path.stat().st_mtime_ns >= src_mtime:
            return cache_path.read_text(encoding="utf-8")
    except OSError:
        pass

    raw = doc.path.read_text(encoding="utf-8", errors="replace")
    text = _strip_for(doc, raw)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="utf-8")
    except OSError:
        # Cache write failure is non-fatal — just slower next time.
        pass
    return text


def build_cache(
    docs: Iterable[Document],
    *,
    max_workers: int = 8,
    force: bool = False,
) -> tuple[int, int]:
    """Populate the text cache for ``docs``.

    Returns (built, skipped) counts. ``force`` ignores existing cache entries.
    """
    from concurrent.futures import ThreadPoolExecutor

    docs_list = list(docs)
    built = 0
    skipped = 0

    def one(d: Document) -> str:
        nonlocal built, skipped
        cache_path = _cache_path_for(d)
        if not force:
            try:
                if (cache_path.exists()
                    and cache_path.stat().st_mtime_ns >= d.path.stat().st_mtime_ns):
                    return "skipped"
            except OSError:
                pass
        try:
            raw = d.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "failed"
        text = _strip_for(d, raw)
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(text, encoding="utf-8")
        except OSError:
            return "failed"
        return "built"

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for outcome in ex.map(one, docs_list, chunksize=64):
            if outcome == "built":
                built += 1
            elif outcome == "skipped":
                skipped += 1
    return built, skipped


# ─── Discovery ─────────────────────────────────────────────────────────────

def discover_gemeenten(root: Path | None = None) -> tuple[tuple[str, str], ...]:
    """Discover municipality folders in the corpus root.

    Older deployments only had the seven groeikernen and the CLI exposed that
    fixed set. The national Kompas corpus keeps the same on-disk shape but
    adds one folder per municipality, so discovery must come from disk.
    """
    root = root or data_root()
    if not root.is_dir():
        return DEFAULT_GEMEENTEN

    out: list[tuple[str, str]] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or path.name.startswith("_"):
            continue
        if (path / "verordeningen").is_dir() or (path / "bekendmakingen").is_dir():
            out.append((path.name, path.name.replace("-", " ").title()))
    return tuple(out) or DEFAULT_GEMEENTEN


def gemeente_slugs(root: Path | None = None) -> frozenset[str]:
    return frozenset(slug for slug, _display in discover_gemeenten(root))

def _parse_md_frontmatter(path: Path) -> tuple[str | None, str | None, str | None]:
    """Return (title, type, modified) from a cleaned-CVDR Markdown header."""
    head = path.read_text(encoding="utf-8", errors="replace")[:1500]
    m_title = re.search(r'^title:\s*"?([^"\n]+?)"?\s*$', head, re.M)
    m_type = re.search(r"^type:\s*(\w+)", head, re.M)
    m_mod = re.search(r"^modified:\s*(\S+)", head, re.M)
    return (
        m_title.group(1) if m_title else None,
        m_type.group(1) if m_type else None,
        m_mod.group(1) if m_mod else None,
    )


def discover_cvdr(root: Path, slug: str) -> list[Document]:
    md_dir = root / slug / "verordeningen" / "md"
    if not md_dir.is_dir():
        return []
    out: list[Document] = []
    for path in md_dir.glob("CVDR*.md"):
        title, ctype, modified = _parse_md_frontmatter(path)
        out.append(Document(
            gemeente=slug,
            source="cvdr",
            doc_id=path.stem,
            path=path,
            cvdr_type=ctype,
            title=title,
            date=modified,
        ))
    return out


def _parse_bekendmakingen_frontmatter(
    path: Path,
) -> tuple[str | None, str | None, str | None]:
    """Return (title, rubriek, date) from a cleaned-bekendmakingen md header."""
    head = path.read_text(encoding="utf-8", errors="replace")[:1500]
    m_title = re.search(r'^title:\s*"?([^"\n]+?)"?\s*$', head, re.M)
    m_rubriek = re.search(r'^rubriek:\s*"?([^"\n]+?)"?\s*$', head, re.M)
    m_date = re.search(r"^date:\s*(\S+)", head, re.M)
    return (
        m_title.group(1) if m_title else None,
        m_rubriek.group(1) if m_rubriek else None,
        m_date.group(1) if m_date else None,
    )


def discover_bekendmakingen(root: Path, slug: str) -> list[Document]:
    # MD only — the national corpus ships cleaned Markdown, never raw HTML.
    # (The old HTML+index.json fallback was dead in prod, where no HTML is
    # mounted, and is removed so "MD only" is a hard guarantee everywhere.)
    md_dir = root / slug / "bekendmakingen" / "md"
    if not md_dir.is_dir():
        return []
    out: list[Document] = []
    for path in md_dir.glob("gmb-*.md"):
        title, rubriek, date = _parse_bekendmakingen_frontmatter(path)
        out.append(Document(
            gemeente=slug,
            source="bekendmakingen",
            doc_id=path.stem,
            path=path,
            rubriek=rubriek,
            title=title,
            date=date,
        ))
    return out


def discover(
    *,
    root: Path | None = None,
    gemeenten: Iterable[str] | None = None,
    sources: Iterable[SourceKind] | None = None,
) -> list[Document]:
    """All indexable docs, filtered by gemeente + source."""
    root = root or data_root()
    available = discover_gemeenten(root)
    sel_g = frozenset(gemeenten) if gemeenten else gemeente_slugs(root)
    sel_s = frozenset(sources) if sources else frozenset(SOURCE_KINDS)
    out: list[Document] = []
    for slug, _display in available:
        if slug not in sel_g:
            continue
        if "cvdr" in sel_s:
            out.extend(discover_cvdr(root, slug))
        if "bekendmakingen" in sel_s:
            out.extend(discover_bekendmakingen(root, slug))
    return out


@dataclass
class Coverage:
    """How many docs of each kind are on disk, per gemeente."""

    by_gemeente: dict[str, dict[str, int]] = field(default_factory=dict)

    def add(self, slug: str, source: SourceKind, count: int) -> None:
        self.by_gemeente.setdefault(slug, {}).update({source: count})

    @property
    def totals(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for slug_counts in self.by_gemeente.values():
            for src, n in slug_counts.items():
                out[src] = out.get(src, 0) + n
        return out


def summarize_coverage(root: Path | None = None) -> Coverage:
    root = root or data_root()
    cov = Coverage()
    for slug, _ in discover_gemeenten(root):
        cov.add(slug, "cvdr", len(discover_cvdr(root, slug)))
        cov.add(slug, "bekendmakingen", len(discover_bekendmakingen(root, slug)))
    return cov
