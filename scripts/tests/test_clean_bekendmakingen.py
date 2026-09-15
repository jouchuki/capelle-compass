"""Tests for the bekendmakingen HTML -> Markdown cleaner.

The cleaner turns the raw KOOP Gemeenteblad detail pages downloaded by
``download_bekendmakingen.py`` into clean Markdown with YAML frontmatter,
mirroring ``clean_cvdr.py`` for the verordeningen corpus.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[1] / "clean_bekendmakingen.py"
_spec = importlib.util.spec_from_file_location("clean_bekendmakingen", _MODULE_PATH)
clean_bekendmakingen = importlib.util.module_from_spec(_spec)
sys.modules["clean_bekendmakingen"] = clean_bekendmakingen  # let dataclasses resolve the module
_spec.loader.exec_module(clean_bekendmakingen)


# A trimmed but structurally faithful Gemeenteblad detail page: chrome +
# sidebar wrap the real publication body, which lives in #broodtekst.
SAMPLE_HTML = """<!DOCTYPE html>
<html><head>
<meta name="DC.identifier" data-scheme="OVERHEIDop.GmbID" content="gmb-2014-8825" />
<title>Verordening Bezwaarschriftencommissie</title>
</head><body>
<div id="header"><nav id="nav"><a href="/">Home</a></nav></div>
<div id="content" role="main" class="content content--publication">
  <div id="broodtekst" class="stuk broodtekst-container">
    <h1 class="staatscourant_kop">Verordening Bezwaarschriftencommissie 2014</h1>
    <div class="hoofdstuk"><h2 class="hoofdstuk_kop">Hoofdstuk 1</h2></div>
    <div class="artikel"><h3 class="artikel_kop">Artikel 1.<br />Begripsbepalingen</h3>
      <p>In deze verordening wordt verstaan onder de commissie.</p>
      <p>De wet is de Algemene wet bestuursrecht.</p></div>
  </div>
</div>
<div id="sidebar"><div id="extrainfo">Gepubliceerd op 19-02-2014</div>
  <div id="gerelateerd">Gerelateerde documenten</div></div>
<footer>Officiele bekendmakingen</footer>
</body></html>"""


def test_extract_body_keeps_publication_text():
    body = clean_bekendmakingen.extract_body(SAMPLE_HTML)
    assert "Begripsbepalingen" in body
    assert "Algemene wet bestuursrecht" in body
    assert "Hoofdstuk 1" in body


def test_extract_body_drops_chrome_and_sidebar():
    body = clean_bekendmakingen.extract_body(SAMPLE_HTML)
    assert "Home" not in body          # nav chrome
    assert "Gerelateerde documenten" not in body  # sidebar
    assert "Officiele bekendmakingen" not in body  # footer


def test_extract_body_preserves_paragraph_breaks():
    body = clean_bekendmakingen.extract_body(SAMPLE_HTML)
    # The two <p> blocks must not be glued into one run-on line.
    assert "commissie.\nDe wet" in body or "commissie.\n\nDe wet" in body


def test_extract_body_returns_empty_when_no_broodtekst():
    assert clean_bekendmakingen.extract_body("<html><body>nothing</body></html>") == ""


def test_extract_body_drops_title_kop_when_marked_on_paragraph():
    # Real KOOP pages carry an empty decoy <h1> then mark the real title with
    # class "staatscourant_kop" on a <p> (not an <h1>). The title is in
    # frontmatter + rendered as the md H1, so the body must not repeat it.
    html = (
        '<div id="broodtekst" class="stuk">'
        "<h1><br /><br /></h1>"
        '<div class="officiele-publicatie"><div class="_p_gemeenteblad">'
        '<p class="staatscourant_kop _p_single-kop-titel">Instellingsbesluit Adviesraad</p>'
        '<div class="artikel"><p class="al">Het college besluit het volgende.</p></div>'
        "</div></div></div>"
    )
    body = clean_bekendmakingen.extract_body(html)
    assert "Het college besluit het volgende." in body
    assert "Instellingsbesluit Adviesraad" not in body


# Real KOOP pages append a boilerplate disclaimer + a footer link menu after
# the publication body, inside #content, with no <footer>/<main> close tag.
DISCLAIMER_FOOTER_HTML = """<html><body>
<div id="content" role="main" class="content content--publication">
  <div id="broodtekst" class="stuk broodtekst-container">
    <div class="artikel"><p>De commissie adviseert het bestuursorgaan.</p></div>
  </div>
  <div class="disclaimer"><div class="disclaimer__content">
    De hier aangeboden elektronische versies worden bij wijze van service aangeboden.
  </div></div>
  <div class="footer row--footer"><ul><li>Over deze website</li>
    <li>Privacy en cookies</li><li>Linked Data Overheid</li></ul></div>
</div></body></html>"""


# The publication container also ends with a "back to top" link and a
# share/permalink modal, all inside #broodtekst after the real text.
MODAL_HTML = """<html><body>
<div id="broodtekst" class="stuk broodtekst-container">
  <div class="artikel"><p>Deze verordening treedt in werking.</p></div>
  <div class="pageactions"><a class="to-top">Naar boven</a>
    <div id="delenModal" class="modal modal--off-screen"><div class="modal__content">
      Permanente link Kopieer de link https://example/gmb-x.html
      <span class="modal__close" id="modalSluiten">Sluit modaal</span>
    </div></div></div>
</div></body></html>"""


def test_extract_body_drops_permalink_modal_widget():
    body = clean_bekendmakingen.extract_body(MODAL_HTML)
    assert "Deze verordening treedt in werking." in body
    assert "Naar boven" not in body
    assert "Permanente link" not in body
    assert "Sluit modaal" not in body


def test_extract_body_drops_disclaimer_and_footer_menu():
    body = clean_bekendmakingen.extract_body(DISCLAIMER_FOOTER_HTML)
    assert "De commissie adviseert het bestuursorgaan." in body
    assert "bij wijze van service" not in body   # KOOP disclaimer boilerplate
    assert "Over deze website" not in body        # footer link menu
    assert "Privacy en cookies" not in body
    assert "Linked Data Overheid" not in body


def test_strip_leading_title_removes_exact_repeat():
    body = "Instellingsbesluit Adviesraad\n\nHet college besluit het volgende."
    out = clean_bekendmakingen.strip_leading_title(body, "Instellingsbesluit Adviesraad")
    assert out.startswith("Het college besluit")
    assert "Instellingsbesluit Adviesraad" not in out


def test_strip_leading_title_ignores_whitespace_and_case_differences():
    body = "INSTELLINGSBESLUIT   adviesraad\nInhoud volgt."
    out = clean_bekendmakingen.strip_leading_title(body, "Instellingsbesluit Adviesraad")
    assert out.strip() == "Inhoud volgt."


def test_strip_leading_title_keeps_body_when_no_repeat():
    body = "Het college van burgemeesters en wethouders besluit."
    out = clean_bekendmakingen.strip_leading_title(body, "Verordening Gevelfonds 2026")
    assert out == body


def test_strip_leading_title_keeps_continuation_after_title_prefix():
    # Short notices repeat the title then continue with the real content on the
    # same line. Stripping must remove only the duplicated title prefix, never
    # the continuation.
    title = "Melding A-evenement Polstraat 1 te Wessem Maasgouw"
    body = (
        "Melding A-evenement Polstraat 1 te Wessem Maasgouw / verzonden 30 maart "
        "2018 / het organiseren van een open dag op 4 en 5 mei."
    )
    out = clean_bekendmakingen.strip_leading_title(body, title)
    assert "verzonden 30 maart 2018" in out
    assert "het organiseren van een open dag" in out
    assert out.strip() != ""


def test_strip_leading_title_does_not_strip_short_generic_prefix():
    # A short title must not eat a real sentence that merely starts with it.
    body = "Besluit van het college over parkeren in de binnenstad."
    out = clean_bekendmakingen.strip_leading_title(body, "Besluit")
    assert out == body


def test_render_markdown_has_frontmatter_and_body():
    pub = clean_bekendmakingen.Publication(
        pub_id="gmb-2014-8825",
        gemeente="capelle-aan-den-ijssel",
        rubriek="algemeen verbindend voorschrift (verordening)",
        title="Verordening Bezwaarschriftencommissie 2014",
        date="2014-02-20",
        detail_url="https://zoek.officielebekendmakingen.nl/gmb-2014-8825.html",
        body="De commissie adviseert.",
    )
    md = clean_bekendmakingen.render_markdown(pub)
    assert md.startswith("---\n")
    assert "pub_id: gmb-2014-8825" in md
    assert "gemeente: capelle-aan-den-ijssel" in md
    assert "rubriek:" in md and "verordening" in md
    assert "date: 2014-02-20" in md
    assert "detail_url: https://zoek.officielebekendmakingen.nl/gmb-2014-8825.html" in md
    # frontmatter title is quoted (contains no colon here but keep it safe),
    # and the body appears after the closing fence + an H1.
    assert "# Verordening Bezwaarschriftencommissie 2014" in md
    assert "De commissie adviseert." in md
    # exactly one frontmatter block
    assert md.count("\n---\n") == 1


def _make_gemeente(tmp_path: Path) -> Path:
    """Build a minimal on-disk bekendmakingen tree for one gemeente."""
    slug = "capelle-aan-den-ijssel"
    bk = tmp_path / slug / "bekendmakingen"
    (bk / "html").mkdir(parents=True)
    (bk / "html" / "gmb-2014-8825.html").write_text(SAMPLE_HTML, encoding="utf-8")
    index = {
        "code": "GM0502",
        "slug": slug,
        "publications": [
            {
                "pub_id": "gmb-2014-8825",
                "rubriek": "algemeen verbindend voorschrift (verordening)",
                "title": "Verordening Bezwaarschriftencommissie 2014",
                "date": "2014-02-20",
                "detail_url": "https://zoek.officielebekendmakingen.nl/gmb-2014-8825.html",
                "file": "gmb-2014-8825.html",
            }
        ],
    }
    (bk / "index.json").write_text(json.dumps(index), encoding="utf-8")
    return tmp_path


def test_process_gemeente_writes_md_from_html_and_index(tmp_path):
    root = _make_gemeente(tmp_path)
    summary = clean_bekendmakingen.process_gemeente(
        "capelle-aan-den-ijssel", root=root, force=False, delete_html=False
    )
    md_path = root / "capelle-aan-den-ijssel" / "bekendmakingen" / "md" / "gmb-2014-8825.md"
    assert md_path.exists()
    text = md_path.read_text(encoding="utf-8")
    assert "pub_id: gmb-2014-8825" in text          # metadata from index.json
    assert "Algemene wet bestuursrecht" in text     # body from html
    assert summary["cleaned"] == 1


def test_process_gemeente_is_idempotent(tmp_path):
    root = _make_gemeente(tmp_path)
    clean_bekendmakingen.process_gemeente(
        "capelle-aan-den-ijssel", root=root, force=False, delete_html=False
    )
    summary = clean_bekendmakingen.process_gemeente(
        "capelle-aan-den-ijssel", root=root, force=False, delete_html=False
    )
    assert summary["cleaned"] == 0
    assert summary["skipped"] == 1


def test_process_gemeente_delete_html_removes_source(tmp_path):
    root = _make_gemeente(tmp_path)
    clean_bekendmakingen.process_gemeente(
        "capelle-aan-den-ijssel", root=root, force=False, delete_html=True
    )
    html_path = root / "capelle-aan-den-ijssel" / "bekendmakingen" / "html" / "gmb-2014-8825.html"
    assert not html_path.exists()
