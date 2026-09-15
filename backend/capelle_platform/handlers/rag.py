"""
RAG (Retrieval-Augmented Generation) handler.

Proxies search requests to the local ChromaDB instance.
In the full LXC deployment this becomes a separate FastAPI service;
for the MVP it's a built-in endpoint sharing the same process.

The ChromaDB client is a lazy-loaded singleton — heavy resource
initialised once per process lifetime.
"""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from capelle_platform.observability import get_logger
from capelle_platform.settings import Settings

_logger = get_logger(__name__)


class RAGHandler:
    """
    Handler for semantic search over the Capelle knowledge base.

    Wraps ChromaDB queries behind a simple REST interface.
    The ChromaDB client is loaded lazily on first request to avoid
    import-time overhead and to survive missing chromadb gracefully.
    """

    def __init__(self, settings: Settings) -> None:
        """
        Construct with the ChromaDB path from Settings.

        The actual client is created lazily — not at init time.
        """
        self._chroma_path = str(settings.chroma_db_path)
        self._client: Any = None
        self._init_lock = threading.Lock()
        self.router = APIRouter(prefix="/api/rag", tags=["rag"])
        self.router.add_api_route("/search", self.search, methods=["GET"])
        self.router.add_api_route("/collections", self.collections, methods=["GET"])

    def _get_client(self) -> Any:
        """
        Lazy-load the ChromaDB PersistentClient as a singleton.

        Uses a threading lock for safe initialisation in case of
        concurrent first-requests.  Falls back gracefully if chromadb
        is not installed.
        """
        if self._client is not None:
            return self._client
        with self._init_lock:
            if self._client is not None:
                return self._client
            try:
                import chromadb
                self._client = chromadb.PersistentClient(path=self._chroma_path)
                _logger.info("chromadb_initialized", path=self._chroma_path)
                return self._client
            except ImportError:
                _logger.error("chromadb_not_installed")
                raise HTTPException(
                    status_code=503,
                    detail="ChromaDB not available",
                )
            except Exception:
                _logger.exception("chromadb_init_failed")
                raise HTTPException(
                    status_code=503,
                    detail="RAG service unavailable",
                )

    async def search(
        self,
        q: str = Query(min_length=2, description="Search query"),
        collection: str = Query(default="policy_documents", description="Collection name"),
        limit: int = Query(default=5, ge=1, le=20, description="Max results"),
    ) -> dict[str, Any]:
        """
        Perform semantic search over a ChromaDB collection.

        Returns the top-N matching documents with their metadata and
        distance scores.
        """
        client = self._get_client()
        try:
            col = client.get_collection(name=collection)
        except Exception:
            raise HTTPException(
                status_code=404,
                detail=f"Collection '{collection}' not found",
            )

        results = col.query(query_texts=[q], n_results=limit)
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        items = []
        for doc, meta, dist in zip(documents, metadatas, distances):
            items.append({
                "content": doc,
                "metadata": meta,
                "distance": dist,
            })

        return {
            "query": q,
            "collection": collection,
            "results": items,
            "total": len(items),
        }

    async def collections(self) -> dict[str, Any]:
        """List all available ChromaDB collections with their document counts."""
        client = self._get_client()
        cols = client.list_collections()
        return {
            "collections": [
                {"name": c.name, "count": c.count()}
                for c in cols
            ]
        }
