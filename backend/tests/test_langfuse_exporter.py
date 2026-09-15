import json

import pytest

from capelle_platform.observability.exporter import LangfuseExporter
from capelle_platform.observability.types import JobMeta
from capelle_platform.settings import Settings

_JWT = "test-secret-minimum-32-characters-long-ok"


def test_langfuse_settings_default_disabled(monkeypatch):
    for k in ("CAPELLE_LANGFUSE_ENABLED", "CAPELLE_LANGFUSE_HOST",
              "CAPELLE_LANGFUSE_PUBLIC_KEY", "CAPELLE_LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CAPELLE_JWT_SECRET", _JWT)
    s = Settings()
    assert s.langfuse_enabled is False
    assert s.langfuse_host == ""


def test_langfuse_settings_from_env(monkeypatch):
    monkeypatch.setenv("CAPELLE_JWT_SECRET", _JWT)
    monkeypatch.setenv("CAPELLE_LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("CAPELLE_LANGFUSE_HOST", "http://langfuse:3000")
    monkeypatch.setenv("CAPELLE_LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("CAPELLE_LANGFUSE_SECRET_KEY", "sk")
    s = Settings()
    assert s.langfuse_enabled is True
    assert s.langfuse_host == "http://langfuse:3000"
    assert s.langfuse_public_key == "pk"


class _FakeClient:
    def __init__(self):
        self.sent = None

    async def send_batch(self, events):
        self.sent = events
        return True


def _write_traj(tmp_path, message_id, rows):
    d = tmp_path / message_id
    d.mkdir(parents=True)
    (d / "trajectory.jsonl").write_text("\n".join(json.dumps(r) for r in rows))


@pytest.mark.asyncio
async def test_disabled_exporter_is_a_noop(tmp_path):
    client = _FakeClient()
    exp = LangfuseExporter(client, jobs_dir=tmp_path, enabled=False)
    await exp.export_job(JobMeta("m1", "groeikern", "u", "s", "q", "completed"))
    assert client.sent is None


@pytest.mark.asyncio
async def test_enabled_exporter_maps_and_sends(tmp_path):
    rows = [
        {"role": "system", "_meta": {"model": "gpt-5.4", "timestamp": "2026-06-11T09:32:31Z"}},
        {"role": "assistant", "content": "hi", "_meta": {"usage": {"input_tokens": 1, "output_tokens": 1}}},
    ]
    _write_traj(tmp_path, "m2", rows)
    client = _FakeClient()
    exp = LangfuseExporter(client, jobs_dir=tmp_path, enabled=True)
    await exp.export_job(JobMeta("m2", "groeikern", "u", "s", "q", "completed"))
    assert client.sent is not None
    assert any(e["type"] == "trace-create" for e in client.sent)


@pytest.mark.asyncio
async def test_missing_trajectory_is_swallowed(tmp_path):
    client = _FakeClient()
    exp = LangfuseExporter(client, jobs_dir=tmp_path, enabled=True)
    await exp.export_job(JobMeta("nope", "groeikern", "u", "s", "q", "failed"))
    assert client.sent is None  # no file -> no send, no raise


def test_worker_pool_accepts_optional_exporter():
    import inspect
    from capelle_platform.worker.pool import AsyncWorkerPool
    sig = inspect.signature(AsyncWorkerPool.__init__)
    assert "exporter" in sig.parameters
    assert sig.parameters["exporter"].default is None
