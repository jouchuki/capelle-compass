"""
Integration tests for the graph internal write endpoints + authenticated read API.

TDD: written FIRST (red), then green once the handler + service are in place.

Testing approach mirrors test_elicitation.py: internal handler methods are
called directly with fake Request objects so the localhost-guard can be
exercised precisely without fighting TestClient's fixed "testclient" host.
The authenticated read API uses TestClient because it is an exterior route
that does not have the loopback guard.

Covers:
1. POST /internal/graph/{message_id}/nodes — happy path → 201 + body + broadcast.
2. POST /internal/graph/{message_id}/nodes — dedup → 409 with duplicate_of.
3. POST /internal/graph/{message_id}/nodes — invalid node (finding without evidence) → 422.
4. POST /internal/graph/{message_id}/nodes — unknown message_id → 404.
5. POST /internal/graph/{message_id}/nodes — non-localhost → 403.
6. POST /internal/graph/{message_id}/nodes — graph_enabled=False → 404.
7. POST /internal/graph/{message_id}/edges — happy path → 201.
8. POST /internal/graph/{message_id}/edges — missing endpoint node → 404.
9. POST /internal/graph/{message_id}/summary — happy path → 204 + broadcast.
10. GET  /internal/graph/{message_id} — full graph.
11. GET  /internal/graph/{message_id}?compact=1 — compact shape.
12. GET /api/chat/sessions/{session_id}/graph — authenticated, 200 with graph.
13. GET /api/chat/sessions/{session_id}/graph — 404 when no graph.
14. GET /api/chat/sessions/{session_id}/graph — 401 when not authenticated.
15. GET /api/chat/sessions/{session_id}/graph — 404 when graph_enabled=False.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from capelle_platform.graph.impl_memory import InMemoryGraphStore
from capelle_platform.graph.models import (
    Confidence,
    EdgeType,
    GraphNode,
    NodeType,
)
from capelle_platform.utils import generate_id

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

_MSG_ID = "msg-abc123"
_SESSION_ID = "sess-xyz789"
_USER_ID = "user-111"


def _make_fake_store(
    message_id: str = _MSG_ID,
    session_id: str = _SESSION_ID,
    user_id: str = _USER_ID,
) -> MagicMock:
    """Build a minimal fake BaseStore that resolves message → session → user."""
    store = MagicMock()

    async def _get_message(mid: str):  # type: ignore[no-untyped-def]
        if mid == message_id:
            msg = MagicMock()
            msg.session_id = session_id
            return msg
        return None

    async def _get_session(sid: str):  # type: ignore[no-untyped-def]
        if sid == session_id:
            sess = MagicMock()
            sess.user_id = user_id
            return sess
        return None

    store.get_message = _get_message
    store.get_session = _get_session
    return store


def _make_fake_request(
    *,
    client_host: str = "127.0.0.1",
    body: dict[str, Any] | None = None,
    compact: bool = False,
) -> MagicMock:
    """Build a minimal fake ``fastapi.Request`` for direct handler calls."""
    req = MagicMock()
    req.client = MagicMock()
    req.client.host = client_host
    if body is not None:
        req.json = AsyncMock(return_value=body)
        req.body = AsyncMock(return_value=json.dumps(body).encode())
    req.headers = {}
    # query_params for ?compact=1
    req.query_params = {"compact": "1"} if compact else {}
    return req


async def _make_service_and_handler(
    *,
    graph_enabled: bool = True,
    message_id: str = _MSG_ID,
    session_id: str = _SESSION_ID,
    user_id: str = _USER_ID,
    graph_store: InMemoryGraphStore | None = None,
    broadcast: AsyncMock | None = None,
) -> tuple[Any, Any, InMemoryGraphStore, AsyncMock]:
    """Build GraphInternalHandler with injected fakes."""
    from capelle_platform.graph.service import GraphService
    from capelle_platform.handlers.graph_internal import GraphInternalHandler
    from capelle_platform.settings import Settings

    if graph_store is None:
        graph_store = InMemoryGraphStore()
    await graph_store.initialize()

    if broadcast is None:
        broadcast = AsyncMock()

    service = GraphService(store=graph_store, broadcast=broadcast)
    store = _make_fake_store(
        message_id=message_id,
        session_id=session_id,
        user_id=user_id,
    )

    settings_mock = MagicMock(spec=Settings)
    settings_mock.graph_enabled = graph_enabled
    # HMAC disabled by default (parity with /internal/ask: empty secret = no-op);
    # a MagicMock attribute would be truthy and wrongly enable signature checks.
    settings_mock.internal_hook_secret = ""

    handler = GraphInternalHandler(
        service=service,
        store=store,
        settings=settings_mock,
    )
    return handler, service, graph_store, broadcast


def _node_payload(
    *,
    node_type: str = "context",
    claim: str = "Testgemeente hanteert een bepaald beleid.",
    confidence: str = "medium",
    blocks: list | None = None,
    citations: list | None = None,
) -> dict[str, Any]:
    """Build a minimal valid POST /nodes body."""
    body: dict[str, Any] = {
        "node_type": node_type,
        "claim": claim,
        "confidence": confidence,
    }
    if blocks is not None:
        body["blocks"] = blocks
    if citations is not None:
        body["citations"] = citations
    return body


def _edge_payload(
    source_id: str,
    target_id: str,
    edge_type: str = "explains",
    rationale: str = "Verband vastgesteld.",
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "target_id": target_id,
        "edge_type": edge_type,
        "rationale": rationale,
    }


# ---------------------------------------------------------------------------
# POST /internal/graph/{message_id}/nodes — happy path
# ---------------------------------------------------------------------------


async def test_add_node_happy_path_returns_201() -> None:
    """POST /nodes with valid context node → 201 with node body."""
    handler, service, store, broadcast = await _make_service_and_handler()
    req = _make_fake_request(body=_node_payload(claim="GRJR regelt de regionale jeugdhulp."))

    resp = await handler.add_node(_MSG_ID, req)

    assert resp.status_code == 201
    body = json.loads(resp.body)
    assert "id" in body
    assert body["claim"] == "GRJR regelt de regionale jeugdhulp."
    assert body["node_type"] == "context"


async def test_add_node_broadcasts_graph_delta() -> None:
    """POST /nodes success calls the broadcast callable."""
    handler, service, store, broadcast = await _make_service_and_handler()
    req = _make_fake_request(body=_node_payload())

    await handler.add_node(_MSG_ID, req)

    broadcast.assert_awaited_once()
    event = broadcast.call_args[0][0]
    assert event["type"] == "graph_delta"
    assert event["kind"] == "node"
    assert event["session_id"] == _SESSION_ID
    assert event["message_id"] == _MSG_ID


async def test_add_node_broadcast_node_persisted() -> None:
    """The node broadcast in the event matches what was stored."""
    handler, service, store, broadcast = await _make_service_and_handler()
    claim = "Capelle groeikern: specifieke claim voor persistentie test."
    req = _make_fake_request(body=_node_payload(claim=claim))

    resp = await handler.add_node(_MSG_ID, req)

    node_id = json.loads(resp.body)["id"]
    graph = await store.get(_SESSION_ID)
    assert graph is not None
    assert any(n.id == node_id for n in graph.nodes)


# ---------------------------------------------------------------------------
# POST /internal/graph/{message_id}/nodes — dedup
# ---------------------------------------------------------------------------


async def test_add_node_dedup_returns_409() -> None:
    """Posting a near-duplicate claim returns 409 with duplicate_of."""
    handler, service, store, broadcast = await _make_service_and_handler()

    original_claim = "Capelle besteedt structureel meer aan jeugdzorg dan vergelijkbare gemeenten."
    req1 = _make_fake_request(body=_node_payload(claim=original_claim))
    resp1 = await handler.add_node(_MSG_ID, req1)
    assert resp1.status_code == 201
    original_id = json.loads(resp1.body)["id"]

    req2 = _make_fake_request(body=_node_payload(claim=original_claim))
    resp2 = await handler.add_node(_MSG_ID, req2)

    assert resp2.status_code == 409
    detail = json.loads(resp2.body)["detail"]
    assert "duplicate_of" in detail
    assert detail["duplicate_of"] == original_id


# ---------------------------------------------------------------------------
# POST /internal/graph/{message_id}/nodes — validation / error paths
# ---------------------------------------------------------------------------


async def test_add_node_invalid_body_returns_422() -> None:
    """Posting a finding node without evidence returns 422."""
    handler, _, _, _ = await _make_service_and_handler()
    req = _make_fake_request(
        body=_node_payload(
            node_type="finding",
            claim="Een bevinding zonder bewijs.",
            # No blocks, no citations → GraphNode model_validator rejects it.
        )
    )

    resp = await handler.add_node(_MSG_ID, req)

    assert resp.status_code == 422


async def test_add_node_unknown_message_returns_404() -> None:
    """Posting to an unknown message_id returns 404."""
    handler, _, _, _ = await _make_service_and_handler()
    req = _make_fake_request(body=_node_payload())

    resp = await handler.add_node("unknown-msg", req)

    assert resp.status_code == 404


async def test_add_node_non_localhost_returns_403() -> None:
    """Requests from non-localhost IPs return 403."""
    handler, _, _, _ = await _make_service_and_handler()
    req = _make_fake_request(
        client_host="10.0.0.1",
        body=_node_payload(),
    )

    resp = await handler.add_node(_MSG_ID, req)

    assert resp.status_code == 403


async def test_add_node_graph_disabled_returns_404() -> None:
    """When graph_enabled=False, POST /nodes returns 404."""
    handler, _, _, _ = await _make_service_and_handler(graph_enabled=False)
    req = _make_fake_request(body=_node_payload())

    resp = await handler.add_node(_MSG_ID, req)

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /internal/graph/{message_id}/edges
# ---------------------------------------------------------------------------


async def _add_node(
    handler: Any, claim: str = "Generiek knooppunt."
) -> str:
    """Helper: add a context node and return its server-assigned id."""
    req = _make_fake_request(body=_node_payload(claim=claim))
    resp = await handler.add_node(_MSG_ID, req)
    assert resp.status_code == 201
    return json.loads(resp.body)["id"]


async def test_add_edge_happy_path_returns_201() -> None:
    """POST /edges with both endpoints in graph → 201 with edge body."""
    handler, service, store, broadcast = await _make_service_and_handler()
    source_id = await _add_node(handler, "GRJR beheert regionale jeugdhulp.")
    target_id = await _add_node(handler, "Capelle betaalt via GRJR bijdrage.")

    req = _make_fake_request(body=_edge_payload(source_id, target_id, edge_type="controls"))
    resp = await handler.add_edge(_MSG_ID, req)

    assert resp.status_code == 201
    body = json.loads(resp.body)
    assert "id" in body
    assert body["source_id"] == source_id
    assert body["target_id"] == target_id


async def test_add_edge_broadcasts_graph_delta() -> None:
    """POST /edges calls broadcast with kind=edge."""
    handler, _, _, broadcast = await _make_service_and_handler()
    source_id = await _add_node(handler, "Bron-knooppunt.")
    target_id = await _add_node(handler, "Doel-knooppunt.")
    broadcast.reset_mock()

    req = _make_fake_request(body=_edge_payload(source_id, target_id))
    await handler.add_edge(_MSG_ID, req)

    broadcast.assert_awaited_once()
    event = broadcast.call_args[0][0]
    assert event["kind"] == "edge"


async def test_add_edge_missing_source_returns_404() -> None:
    """POST /edges with unknown source_id → 404."""
    handler, _, _, _ = await _make_service_and_handler()
    target_id = await _add_node(handler, "Bestaand doel.")

    req = _make_fake_request(body=_edge_payload("ghost-source", target_id))
    resp = await handler.add_edge(_MSG_ID, req)

    assert resp.status_code == 404


async def test_add_edge_missing_target_returns_404() -> None:
    """POST /edges with unknown target_id → 404."""
    handler, _, _, _ = await _make_service_and_handler()
    source_id = await _add_node(handler, "Bestaande bron.")

    req = _make_fake_request(body=_edge_payload(source_id, "ghost-target"))
    resp = await handler.add_edge(_MSG_ID, req)

    assert resp.status_code == 404


async def test_add_edge_graph_disabled_returns_404() -> None:
    """When graph_enabled=False, POST /edges returns 404."""
    handler, _, _, _ = await _make_service_and_handler(graph_enabled=False)
    req = _make_fake_request(body=_edge_payload("a", "b"))

    resp = await handler.add_edge(_MSG_ID, req)

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /internal/graph/{message_id}/summary
# ---------------------------------------------------------------------------


async def test_set_summary_happy_path_returns_204() -> None:
    """POST /summary → 204 No Content."""
    handler, _, _, _ = await _make_service_and_handler()
    req = _make_fake_request(body={"text": "Fase 1: context verzameld, 3 knooppunten."})

    resp = await handler.set_summary(_MSG_ID, req)

    assert resp.status_code == 204


async def test_set_summary_broadcasts() -> None:
    """POST /summary calls broadcast with kind=summary."""
    handler, _, _, broadcast = await _make_service_and_handler()
    req = _make_fake_request(body={"text": "Samenvatting."})

    await handler.set_summary(_MSG_ID, req)

    broadcast.assert_awaited_once()
    event = broadcast.call_args[0][0]
    assert event["kind"] == "summary"
    assert event["data"] == "Samenvatting."


async def test_set_summary_unknown_message_returns_404() -> None:
    """POST /summary for unknown message_id → 404."""
    handler, _, _, _ = await _make_service_and_handler()
    req = _make_fake_request(body={"text": "Iets."})

    resp = await handler.set_summary("unknown-id", req)

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /internal/graph/{message_id}
# ---------------------------------------------------------------------------


async def test_get_graph_full() -> None:
    """GET /internal/graph/{message_id} returns the full FindingGraph JSON."""
    handler, _, _, _ = await _make_service_and_handler()
    await _add_node(handler, "Eerste knooppunt voor full-graph test.")

    req = _make_fake_request()
    resp = await handler.get_graph(_MSG_ID, req)

    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert "nodes" in body
    assert "edges" in body
    assert len(body["nodes"]) >= 1


async def test_get_graph_compact() -> None:
    """GET /internal/graph/{message_id}?compact=1 returns compact shape."""
    handler, _, _, _ = await _make_service_and_handler()
    await _add_node(handler, "Compact knooppunt.")

    req = _make_fake_request(compact=True)
    resp = await handler.get_graph(_MSG_ID, req, compact=True)

    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert "nodes" in body
    # Each compact node has id, claim, node_type only.
    assert set(body["nodes"][0].keys()) == {"id", "claim", "node_type"}
    # Edges is a list of lists (serialized arrays in JSON).
    assert "edges" in body


async def test_get_graph_empty_session_returns_200_with_empty() -> None:
    """GET /internal/graph/{message_id} when no graph yet → 200 with empty nodes."""
    handler, _, _, _ = await _make_service_and_handler()
    req = _make_fake_request()

    resp = await handler.get_graph(_MSG_ID, req)

    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["nodes"] == []


async def test_get_graph_unknown_message_returns_404() -> None:
    """GET /internal/graph/{message_id} for unknown message → 404."""
    handler, _, _, _ = await _make_service_and_handler()
    req = _make_fake_request()

    resp = await handler.get_graph("unknown-msg", req)

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/chat/sessions/{session_id}/graph — authenticated read API
# ---------------------------------------------------------------------------


def _make_read_app(
    *,
    graph_enabled: bool = True,
    graph_store: InMemoryGraphStore,
    auth_user_id: str = _USER_ID,
    session_id: str = _SESSION_ID,
    session_owner_id: str = _USER_ID,
) -> FastAPI:
    """Build a FastAPI app with only the GraphReadHandler wired."""
    from capelle_platform.graph.service import GraphService
    from capelle_platform.handlers.graph_read import GraphReadHandler
    from capelle_platform.settings import Settings

    app = FastAPI()
    broadcast = AsyncMock()
    service = GraphService(store=graph_store, broadcast=broadcast)

    fake_store = MagicMock()

    async def _get_session(sid: str):  # type: ignore[no-untyped-def]
        if sid == session_id:
            sess = MagicMock()
            sess.user_id = session_owner_id
            return sess
        return None

    fake_store.get_session = _get_session

    settings_mock = MagicMock(spec=Settings)
    settings_mock.graph_enabled = graph_enabled
    settings_mock.auth_cookie_name = "capelle_auth"

    auth = MagicMock()

    def _validate_token(token: str) -> Any:
        if token == "valid-token":
            user = MagicMock()
            user.id = auth_user_id
            return user
        raise ValueError("invalid token")

    auth.validate_token = _validate_token

    read_handler = GraphReadHandler(
        service=service,
        store=fake_store,
        authenticator=auth,
        settings=settings_mock,
    )
    app.include_router(read_handler.router)
    return app


def test_read_graph_authenticated_returns_200() -> None:
    """GET /api/chat/sessions/{session_id}/graph with valid token returns 200."""
    graph_store = InMemoryGraphStore()

    # Seed the graph directly (bypass the internal handler's host guard).
    async def _seed():
        from capelle_platform.graph.service import GraphService
        svc = GraphService(store=graph_store, broadcast=AsyncMock())
        node = GraphNode(
            id=generate_id(),
            node_type=NodeType.CONTEXT,
            claim="Bevinding voor read API test.",
            confidence=Confidence.MEDIUM,
        )
        await graph_store.initialize()
        await svc.add_node(_SESSION_ID, _MSG_ID, node)

    asyncio.get_event_loop().run_until_complete(_seed())

    app = _make_read_app(graph_store=graph_store)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get(
        f"/api/chat/sessions/{_SESSION_ID}/graph",
        headers={"Authorization": "Bearer valid-token"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "nodes" in body
    assert len(body["nodes"]) >= 1


def test_read_graph_no_graph_returns_404() -> None:
    """GET /api/chat/sessions/{session_id}/graph returns 404 when no graph exists."""
    graph_store = InMemoryGraphStore()
    asyncio.get_event_loop().run_until_complete(graph_store.initialize())

    app = _make_read_app(graph_store=graph_store)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get(
        f"/api/chat/sessions/{_SESSION_ID}/graph",
        headers={"Authorization": "Bearer valid-token"},
    )
    assert resp.status_code == 404


def test_read_graph_unauthenticated_returns_401() -> None:
    """GET /api/chat/sessions/{session_id}/graph without token returns 401."""
    graph_store = InMemoryGraphStore()
    asyncio.get_event_loop().run_until_complete(graph_store.initialize())

    app = _make_read_app(graph_store=graph_store)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get(f"/api/chat/sessions/{_SESSION_ID}/graph")
    assert resp.status_code == 401


def test_read_graph_graph_disabled_returns_404() -> None:
    """GET /api/chat/sessions/{session_id}/graph when graph_enabled=False → 404."""
    graph_store = InMemoryGraphStore()
    asyncio.get_event_loop().run_until_complete(graph_store.initialize())

    app = _make_read_app(graph_store=graph_store, graph_enabled=False)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get(
        f"/api/chat/sessions/{_SESSION_ID}/graph",
        headers={"Authorization": "Bearer valid-token"},
    )
    assert resp.status_code == 404


def test_read_graph_wrong_user_returns_403() -> None:
    """GET /api/chat/sessions/{session_id}/graph for a session owned by different user → 403."""
    graph_store = InMemoryGraphStore()

    async def _seed():
        from capelle_platform.graph.service import GraphService
        svc = GraphService(store=graph_store, broadcast=AsyncMock())
        node = GraphNode(
            id=generate_id(),
            node_type=NodeType.CONTEXT,
            claim="Bevinding voor auth test.",
            confidence=Confidence.MEDIUM,
        )
        await graph_store.initialize()
        await svc.add_node(_SESSION_ID, _MSG_ID, node)

    asyncio.get_event_loop().run_until_complete(_seed())

    # auth_user_id is "other-user" but session_owner_id is _USER_ID.
    app = _make_read_app(
        graph_store=graph_store,
        auth_user_id="other-user",
        session_owner_id=_USER_ID,
    )
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get(
        f"/api/chat/sessions/{_SESSION_ID}/graph",
        headers={"Authorization": "Bearer valid-token"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# HMAC signature parity with /internal/ask (defence-in-depth, API-5)
# ---------------------------------------------------------------------------


async def test_add_node_rejects_missing_signature_when_secret_set() -> None:
    """With internal_hook_secret configured, an unsigned write is 403."""
    handler, _service, _gs, _bc = await _make_service_and_handler()
    handler._settings.internal_hook_secret = "s3cret-s3cret-s3cret"
    req = _make_fake_request(body=_node_payload())
    resp = await handler.add_node(_MSG_ID, req)
    assert resp.status_code == 403


async def test_add_node_accepts_valid_signature_when_secret_set() -> None:
    """A correctly signed write passes the HMAC guard (parity with /internal/ask)."""
    from capelle_platform.utils.hook_auth import (
        HOOK_SIGNATURE_HEADER,
        sign_message_id,
    )

    handler, _service, _gs, _bc = await _make_service_and_handler()
    secret = "s3cret-s3cret-s3cret"
    handler._settings.internal_hook_secret = secret
    req = _make_fake_request(body=_node_payload())
    req.headers = {HOOK_SIGNATURE_HEADER: sign_message_id(secret, _MSG_ID)}
    resp = await handler.add_node(_MSG_ID, req)
    assert resp.status_code == 201
