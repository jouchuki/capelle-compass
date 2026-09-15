"""Paths for the CBS RAG index.

``CHROMA_PATH`` is the unified on-disk location shared with other Capelle
tools. It can be overridden via the ``CAPELLE_CHROMA_PATH`` environment
variable (honoured by :func:`cbs_tool.rag.client.get_chroma_client`).

When ``CAPELLE_CHROMA_HTTP`` is set, the on-disk path is unused — the client
talks to a remote Chroma server instead.
"""
from __future__ import annotations

import os
from pathlib import Path

CBS_DATA_DIR = Path(os.environ.get("CBS_DATA_DIR", str(Path(__file__).resolve().parents[2] / "reference" / "cbs")))
ENRICHED_PATH = CBS_DATA_DIR / "enriched_catalog.jsonl"


def _chroma_path() -> Path:
    env = os.environ.get("CAPELLE_CHROMA_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "capelle_rag" / "chroma_db"


CHROMA_PATH = _chroma_path()
