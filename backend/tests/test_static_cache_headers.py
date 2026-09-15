"""Cache-Control headers on the SPA static mount.

index.html must be revalidated on every load (else a cached index pins the
browser to a stale content-hashed bundle across deploys); /assets/* are
content-hashed and may be cached immutably.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from capelle_platform import static_mount


def _build_client(tmp_path: Path) -> TestClient:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>app</title>", encoding="utf-8")
    (dist / "assets" / "index-DEADBEEF.js").write_text("console.log(1)", encoding="utf-8")
    static_mount.DIST_DIR = dist  # module global read at request time
    app = FastAPI()
    static_mount.mount_frontend(app)
    return TestClient(app)


def test_index_html_is_no_cache(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "no-cache" in resp.headers.get("cache-control", "")


def test_spa_fallback_route_is_no_cache(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    resp = client.get("/some/client/route")
    assert resp.status_code == 200
    assert "no-cache" in resp.headers.get("cache-control", "")


def test_hashed_asset_is_immutable(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    resp = client.get("/assets/index-DEADBEEF.js")
    assert resp.status_code == 200
    cc = resp.headers.get("cache-control", "")
    assert "immutable" in cc and "max-age=31536000" in cc
