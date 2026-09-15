"""Tests for the gemeente selection logic in ``download_openspending.py``.

These tests exercise ONLY the pure selection function ``_select_gemeenten``.
They never touch the network: ``_select_gemeenten`` reads the canonical
municipality list from disk (``data/nederlandse_gemeenten.json``) and the
hardcoded groeikernen tuple — no HTTP is involved.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[1] / "download_openspending.py"
_spec = importlib.util.spec_from_file_location("download_openspending", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
download_openspending = importlib.util.module_from_spec(_spec)
sys.modules["download_openspending"] = download_openspending  # let dataclasses resolve the module
_spec.loader.exec_module(download_openspending)


def test_select_gemeenten_default_is_seven() -> None:
    selection = download_openspending._select_gemeenten(all_gemeenten=False)
    assert len(selection) == 7
    assert ("GM0502", "capelle-aan-den-ijssel") in selection


def test_select_gemeenten_all_is_national() -> None:
    selection = download_openspending._select_gemeenten(all_gemeenten=True)
    assert len(selection) >= 342
    slugs = {slug for _, slug in selection}
    assert "capelle-aan-den-ijssel" in slugs
    assert "amsterdam" in slugs


def test_select_gemeenten_returns_code_slug_pairs() -> None:
    selection = download_openspending._select_gemeenten(all_gemeenten=True)
    for entry in selection:
        assert isinstance(entry, tuple)
        assert len(entry) == 2
        code, slug = entry
        assert isinstance(code, str) and code.startswith("GM")
        assert isinstance(slug, str) and slug
