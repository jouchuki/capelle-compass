"""
Serve the built React frontend from the FastAPI app.

The SPA uses client-side routing (plain ``/``, fork-share links at
``/f/<token>``, etc.), so every non-API path must ultimately resolve
to ``index.html``. Asset requests (bundles under ``/assets/``) are
served from disk directly; unknown paths fall through to the SPA
entrypoint so the React router can take over.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse



DIST_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"

# index.html must ALWAYS be revalidated: it names the content-hashed bundle
# (e.g. index-BS-fpEOX.js), so a cached index.html pins the browser to a stale
# bundle across deploys. ``no-cache`` means "revalidate every time" (the etag
# yields a cheap 304 when unchanged), not "never store".
_INDEX_CACHE = {"Cache-Control": "no-cache, must-revalidate"}
# Files under /assets/ are content-hashed by Vite, so their bytes never change
# for a given name — safe to cache for a year.
_ASSET_CACHE = {"Cache-Control": "public, max-age=31536000, immutable"}


def _index_response(root: Path) -> FileResponse:
    return FileResponse(root / "index.html", headers=_INDEX_CACHE)


def mount_frontend(app: FastAPI) -> None:
    """
    Attach a catch-all route that serves the SPA bundle.

    Registered LAST so API routes defined beforehand win on exact matches.
    Does nothing when the dist directory is absent — safe to call
    unconditionally at startup.
    """
    if not DIST_DIR.exists():
        return

    @app.get("/{path:path}", include_in_schema=False)
    async def serve_frontend(request: Request, path: str) -> FileResponse:
        """Return the requested asset or fall through to index.html."""
        root = DIST_DIR
        # Guard against path traversal: resolve the candidate and ensure
        # it is inside the SELECTED root before trusting it.
        candidate = (root / path).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            return _index_response(root)
        if candidate.is_file():
            if candidate.name == "index.html":
                return _index_response(root)
            headers = _ASSET_CACHE if path.startswith("assets/") else None
            return FileResponse(candidate, headers=headers)
        return _index_response(root)
