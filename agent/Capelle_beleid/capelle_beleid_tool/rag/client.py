"""Chroma client factory — HttpClient if CAPELLE_CHROMA_HTTP env var set, else PersistentClient.

The client is shared across Capelle tools. Behaviour:
  - If ``CAPELLE_CHROMA_HTTP`` is set (e.g. ``127.0.0.1:8000``), return an HttpClient.
  - Otherwise return a PersistentClient rooted at ``CAPELLE_CHROMA_PATH`` if set,
    else the unified repo path ``agent/capelle_rag/chroma_db``.

HttpClient and PersistentClient are API-compatible, so collection-reading code
does not need to change once it obtains a client via :func:`get_chroma_client`.
"""
from __future__ import annotations

import os
from pathlib import Path

import chromadb
from chromadb.api import ClientAPI


def _default_path() -> Path:
    """Return the unified on-disk Chroma path for dev machines."""
    env = os.environ.get("CAPELLE_CHROMA_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "capelle_rag" / "chroma_db"


def get_chroma_client() -> ClientAPI:
    """Return a Chroma client.

    Uses :class:`chromadb.HttpClient` if ``CAPELLE_CHROMA_HTTP`` is set
    (``host[:port]``), otherwise :class:`chromadb.PersistentClient` at the
    unified path.
    """
    http = os.environ.get("CAPELLE_CHROMA_HTTP")
    if http:
        host, _, port_str = http.partition(":")
        return chromadb.HttpClient(host=host or "127.0.0.1", port=int(port_str or "8000"))
    path = _default_path()
    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(path))
