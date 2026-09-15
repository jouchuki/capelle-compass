"""Tests for bekendmakingen md discovery + type-aware text stripping.

The national corpus ships cleaned Markdown (no raw HTML) to the yutas, so the
tool discovers bekendmakingen as ``md/gmb-*.md`` and strips frontmatter from
them. Raw HTML is never indexed — even when an ``html/`` tree is present it is
ignored (MD-only is a hard guarantee).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from capelle_groeikernen_tool import corpus  # noqa: E402


def _write_bekendmakingen_md(root: Path, slug: str) -> Path:
    md_dir = root / slug / "bekendmakingen" / "md"
    md_dir.mkdir(parents=True)
    (md_dir / "gmb-2026-208813.md").write_text(
        "---\n"
        "pub_id: gmb-2026-208813\n"
        f"gemeente: {slug}\n"
        'rubriek: "algemeen verbindend voorschrift (verordening)"\n'
        'title: "Verordening Gevelfonds 2026"\n'
        "date: 2026-05-01\n"
        "detail_url: https://zoek.officielebekendmakingen.nl/gmb-2026-208813.html\n"
        "---\n\n"
        "# Verordening Gevelfonds 2026\n\n"
        "De raad besluit tot instelling van een gevelfonds.\n",
        encoding="utf-8",
    )
    return root


def _write_bekendmakingen_html(root: Path, slug: str) -> Path:
    bk = root / slug / "bekendmakingen"
    (bk / "html").mkdir(parents=True)
    (bk / "html" / "gmb-2020-1.html").write_text(
        "<html><body><div id='broodtekst'>oude html publicatie</div></body></html>",
        encoding="utf-8",
    )
    (bk / "index.json").write_text(
        json.dumps({"publications": [
            {"pub_id": "gmb-2020-1", "rubriek": "beleidsregel",
             "title": "Oude regeling", "date": "2020-01-01"}
        ]}),
        encoding="utf-8",
    )
    return root


def test_discover_bekendmakingen_prefers_md(tmp_path):
    root = _write_bekendmakingen_md(tmp_path, "druten")
    docs = corpus.discover_bekendmakingen(root, "druten")
    assert len(docs) == 1
    doc = docs[0]
    assert doc.source == "bekendmakingen"
    assert doc.doc_id == "gmb-2026-208813"
    assert doc.path.suffix == ".md"
    # metadata lifted from frontmatter
    assert doc.rubriek == "algemeen verbindend voorschrift (verordening)"
    assert doc.title == "Verordening Gevelfonds 2026"
    assert doc.date == "2026-05-01"


def test_discover_bekendmakingen_ignores_html(tmp_path):
    # Only a legacy html/ + index.json layout present, no md/ → nothing indexed.
    root = _write_bekendmakingen_html(tmp_path, "brunssum")
    docs = corpus.discover_bekendmakingen(root, "brunssum")
    assert docs == []


def test_discover_bekendmakingen_md_only_even_with_html_present(tmp_path):
    # md/ and a stray html/ tree side by side → only the md doc is returned.
    root = _write_bekendmakingen_md(tmp_path, "druten")
    _write_bekendmakingen_html(root, "druten")
    docs = corpus.discover_bekendmakingen(root, "druten")
    assert len(docs) == 1
    assert docs[0].path.suffix == ".md"


def test_strip_for_md_bekendmakingen_drops_frontmatter(tmp_path):
    root = _write_bekendmakingen_md(tmp_path, "druten")
    doc = corpus.discover_bekendmakingen(root, "druten")[0]
    raw = doc.path.read_text(encoding="utf-8")
    body = corpus._strip_for(doc, raw)
    assert "pub_id:" not in body            # frontmatter gone
    assert "rubriek:" not in body
    assert "gevelfonds" in body.lower()      # body text kept
