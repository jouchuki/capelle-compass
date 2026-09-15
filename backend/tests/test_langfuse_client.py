import base64

import httpx
import pytest

from capelle_platform.observability.client import LangfuseClient


@pytest.mark.asyncio
async def test_send_batch_posts_with_basic_auth_and_envelope():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = request.read()
        return httpx.Response(207, json={"successes": [], "errors": []})

    transport = httpx.MockTransport(handler)
    client = LangfuseClient("http://langfuse:3000", "pk", "sk", transport=transport)

    ok = await client.send_batch([{"id": "e1", "type": "trace-create", "body": {}}])

    assert ok is True
    assert captured["url"].endswith("/api/public/ingestion")
    assert captured["auth"] == "Basic " + base64.b64encode(b"pk:sk").decode()
    assert b'"batch"' in captured["body"]


@pytest.mark.asyncio
async def test_send_batch_swallows_transport_errors():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    client = LangfuseClient("http://x", "pk", "sk", transport=httpx.MockTransport(boom))
    assert await client.send_batch([{"id": "e1", "type": "trace-create", "body": {}}]) is False


@pytest.mark.asyncio
async def test_empty_batch_is_a_noop_success():
    client = LangfuseClient("http://x", "pk", "sk")
    assert await client.send_batch([]) is True
