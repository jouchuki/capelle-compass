"""Application startup, authentication, and request validation."""
from fastapi.testclient import TestClient
from capelle_platform import static_mount
from capelle_platform.builder import AppBuilder
from capelle_platform.models.mode import SUPPORTED_MODES
from capelle_platform.web.host import domain_for_host


def test_startup_auth_and_session_validation(settings_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(static_mount, "DIST_DIR", tmp_path / "missing-dist")
    settings = settings_factory(auth_cookie_secure=False, public_origin="http://testserver")
    app = AppBuilder(settings).build()
    assert SUPPORTED_MODES == ('groeikern', 'jeugdzorg')
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        result = client.post("/api/auth/register", json={"email": "reviewer@example.com", "password": "review-password"})
        assert result.status_code in (200, 201), result.text
        headers = {"Authorization": "Bearer " + result.json()["access_token"]}
        for mode in ('groeikern', 'jeugdzorg'):
            created = client.post("/api/chat/sessions", json={"mode": mode}, headers=headers)
            assert created.status_code == 201, created.text
            assert created.json()["domain"] == 'capelle'
        for mode in ('invalid-mode',):
            denied = client.post("/api/chat/sessions", json={"mode": mode}, headers=headers)
            assert denied.status_code == 422
        # Session listings remain consistent through proxy aliases.
        listing = client.get("/api/chat/sessions", headers={**headers, "Host": "other.example:8443"})
        assert listing.status_code == 200
        assert listing.json()["total"] == len(('groeikern', 'jeugdzorg'))


def test_frontend_and_namespace_ignore_hostname(tmp_path, monkeypatch):
    from fastapi import FastAPI
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("compass-app")
    secret = tmp_path / "secret.txt"
    secret.write_text("private-outside-bundle")
    (dist / "escape.txt").symlink_to(secret)
    monkeypatch.setattr(static_mount, "DIST_DIR", dist)
    app = FastAPI()
    static_mount.mount_frontend(app)
    with TestClient(app) as client:
        for host in ("localhost", "other.example", "OTHER.EXAMPLE:443"):
            assert domain_for_host(host) == 'capelle'
            response = client.get("/", headers={"Host": host})
            assert response.text == "compass-app"
            assert "no-cache" in response.headers["cache-control"]
        assert client.get("/escape.txt").text == "compass-app"
