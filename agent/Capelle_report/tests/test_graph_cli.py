"""
Tests for ``capelle-graph`` CLI (capelle_report_tool.graph).

Uses a local HTTP server stub (via http.server in a background thread) to
avoid any calls to the real platform.  No monkeypatching of the HTTP layer —
the real ``urllib.request`` machinery exercises our actual code paths.

Covered:
1. Batch add happy path: ref-resolution, node ids captured, edges posted
   with resolved ids.
2. Batch add 409 → linked_existing: the 409 ref is mapped to the
   ``duplicate_of`` id so subsequent edges still resolve correctly.
3. Batch add edge with unknown ref passes the raw ref through as-is.
4. Batch add all-failed → exit 3.
5. Batch add partial success (some nodes fail) → exit 0 (something succeeded).
6. add-node granular: happy path → {"node_id": ...}, exit 0.
7. add-node granular: 409 → {"duplicate_of": ...}, exit 0.
8. add-edge granular: happy path → edge JSON, exit 0.
9. set-summary: 204 → exit 0.
10. list --compact: 200 → JSON to stdout, exit 0.
11. Server unreachable → exit 3.
12. CAPELLE_MESSAGE_ID missing → exit 2.
13. Batch file not found → exit 2.
14. graph feature disabled (404 with "disabled") → exit 3.
"""

from __future__ import annotations

import http.server
import json
import os
import tempfile
import threading
from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest

from capelle_report_tool.graph import (
    _EXIT_OK,
    _EXIT_SERVER,
    _EXIT_USAGE,
    main,
)

# ---------------------------------------------------------------------------
# Stub HTTP server
# ---------------------------------------------------------------------------

_FAKE_NODE_ID = "aabbccdd1122"
_FAKE_DUPLICATE_ID = "ddeeff334455"
_FAKE_EDGE_ID = "112233445566"


class _StubHandler(http.server.BaseHTTPRequestHandler):
    """
    Minimal HTTP stub that records requests and returns pre-programmed
    responses.  Responses are controlled via the class-level ``responses``
    dict keyed by ``(METHOD, path_prefix)``.

    Designed to exercise:
    - POST /internal/graph/{msg}/nodes  → 201 or 409
    - POST /internal/graph/{msg}/edges  → 201
    - POST /internal/graph/{msg}/summary → 204
    - GET  /internal/graph/{msg}        → 200
    """

    # Class-level state shared across instances within a test.
    responses: dict[str, tuple[int, Any]] = {}
    requests_received: list[dict[str, Any]] = []

    def log_message(self, *_: Any) -> None:  # suppress stdout noise
        pass

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length:
            raw = self.rfile.read(length)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}
        return {}

    def _respond(self, status: int, body: Any) -> None:
        if status == 204:
            self.send_response(204)
            self.end_headers()
            return
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _dispatch(self, method: str) -> None:
        body = self._read_body() if method == "POST" else {}
        _StubHandler.requests_received.append(
            {"method": method, "path": self.path, "body": body}
        )

        # Try exact path, then strip query string.
        key = (method, self.path)
        if key not in _StubHandler.responses:
            path_no_qs = self.path.split("?")[0]
            key = (method, path_no_qs)
        if key in _StubHandler.responses:
            status, resp_body = _StubHandler.responses[key]
            self._respond(status, resp_body)
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 — required by BaseHTTPRequestHandler
        self._dispatch("POST")

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")


def _make_server(responses: dict[str, tuple[int, Any]]) -> tuple[str, threading.Thread]:
    """
    Start a one-shot stub HTTP server on a free port.

    ``responses`` keys are ``"METHOD /path"`` strings.

    Returns ``(base_url, thread)``.  The server runs in a daemon thread;
    call ``server.shutdown()`` to stop it cleanly from the fixture.
    """
    _StubHandler.responses = {
        (k.split(" ", 1)[0], k.split(" ", 1)[1]): v
        for k, v in responses.items()
    }
    _StubHandler.requests_received = []

    srv = http.server.HTTPServer(("127.0.0.1", 0), _StubHandler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{port}", srv


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def env_with_message_id(monkeypatch: pytest.MonkeyPatch):
    """Set CAPELLE_MESSAGE_ID + CAPELLE_INTERNAL_URL in the environment."""
    monkeypatch.setenv("CAPELLE_MESSAGE_ID", "msg-test-001")

    def _factory(base_url: str) -> None:
        monkeypatch.setenv("CAPELLE_INTERNAL_URL", base_url)

    return _factory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node_path(msg: str = "msg-test-001") -> str:
    return f"/internal/graph/{msg}/nodes"


def _edge_path(msg: str = "msg-test-001") -> str:
    return f"/internal/graph/{msg}/edges"


def _summary_path(msg: str = "msg-test-001") -> str:
    return f"/internal/graph/{msg}/summary"


def _graph_path(msg: str = "msg-test-001") -> str:
    return f"/internal/graph/{msg}"


def _graph_compact_path(msg: str = "msg-test-001") -> str:
    return f"/internal/graph/{msg}?compact=1"


def _write_findings(tmp_path: Any, doc: dict[str, Any]) -> str:
    p = tmp_path / "findings.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# 1. Batch add happy path
# ---------------------------------------------------------------------------


def test_batch_add_happy_path(
    env_with_message_id: Any,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Nodes are POSTed in order; edges get resolved ids."""
    node_a_id = "aaa000000001"
    node_b_id = "bbb000000002"
    edge_id = "ccc000000003"

    responses = {
        f"POST {_node_path()}": (201, {"id": node_a_id, "claim": "Node A", "node_type": "context"}),
    }

    # We need the stub to return different ids for successive POSTs.  Use a
    # counter via a custom handler subclass for this test only.
    node_ids = [node_a_id, node_b_id]
    call_counter: list[int] = [0]

    class _CountingHandler(_StubHandler):
        def _dispatch(self, method: str) -> None:  # type: ignore[override]
            body = self._read_body() if method == "POST" else {}
            _StubHandler.requests_received.append(
                {"method": method, "path": self.path, "body": body}
            )
            if method == "POST" and self.path == _node_path():
                idx = call_counter[0]
                call_counter[0] += 1
                nid = node_ids[idx] if idx < len(node_ids) else "extra"
                self._respond(201, {"id": nid, "claim": "x", "node_type": "context"})
            elif method == "POST" and self.path == _edge_path():
                self._respond(201, {"id": edge_id, "source_id": node_a_id, "target_id": node_b_id, "edge_type": "controls"})
            else:
                self._respond(404, {"error": "not found"})

    srv = http.server.HTTPServer(("127.0.0.1", 0), _CountingHandler)
    _StubHandler.requests_received = []
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base_url = f"http://127.0.0.1:{port}"

    try:
        env_with_message_id(base_url)
        doc = {
            "nodes": [
                {"ref": "nodeA", "type": "context", "claim": "Claim A"},
                {"ref": "nodeB", "type": "context", "claim": "Claim B"},
            ],
            "edges": [
                {"source": "nodeA", "target": "nodeB", "type": "controls", "rationale": "A controls B"}
            ],
        }
        findings_file = _write_findings(tmp_path, doc)
        rc = main(["add", findings_file])
    finally:
        srv.shutdown()

    assert rc == _EXIT_OK
    out = capsys.readouterr().out
    result = json.loads(out)
    assert result["nodes"]["nodeA"]["id"] == node_a_id
    assert result["nodes"]["nodeA"]["status"] == "created"
    assert result["nodes"]["nodeB"]["id"] == node_b_id
    assert result["edges"][0]["status"] == "created"
    # Verify edge was posted with resolved ids.
    edge_posts = [
        r for r in _StubHandler.requests_received
        if r["method"] == "POST" and r["path"] == _edge_path()
    ]
    assert len(edge_posts) == 1
    assert edge_posts[0]["body"]["source_id"] == node_a_id
    assert edge_posts[0]["body"]["target_id"] == node_b_id


# ---------------------------------------------------------------------------
# 2. Batch add 409 → linked_existing
# ---------------------------------------------------------------------------


def test_batch_add_409_maps_to_linked_existing(
    env_with_message_id: Any,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A 409 on a node maps the ref to duplicate_of; subsequent edges resolve correctly."""
    dup_id = _FAKE_DUPLICATE_ID
    edge_id = _FAKE_EDGE_ID
    context_id = "ctx000000001"

    node_call_count: list[int] = [0]

    class _DupHandler(_StubHandler):
        def _dispatch(self, method: str) -> None:  # type: ignore[override]
            body = self._read_body() if method == "POST" else {}
            _StubHandler.requests_received.append(
                {"method": method, "path": self.path, "body": body}
            )
            if method == "POST" and self.path == _node_path():
                idx = node_call_count[0]
                node_call_count[0] += 1
                if idx == 0:
                    # First node: duplicate
                    self._respond(409, {"detail": {"duplicate_of": dup_id}})
                else:
                    # Second node: fresh
                    self._respond(201, {"id": context_id, "claim": "ctx", "node_type": "context"})
            elif method == "POST" and self.path == _edge_path():
                self._respond(201, {"id": edge_id, "source_id": dup_id, "target_id": context_id, "edge_type": "explains"})
            else:
                self._respond(404, {"error": "not found"})

    srv = http.server.HTTPServer(("127.0.0.1", 0), _DupHandler)
    _StubHandler.requests_received = []
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    try:
        env_with_message_id(f"http://127.0.0.1:{port}")
        doc = {
            "nodes": [
                {"ref": "dupNode", "type": "context", "claim": "Duplicate claim"},
                {"ref": "ctxNode", "type": "context", "claim": "New context"},
            ],
            "edges": [
                {"source": "dupNode", "target": "ctxNode", "type": "explains", "rationale": "r"}
            ],
        }
        findings_file = _write_findings(tmp_path, doc)
        rc = main(["add", findings_file])
    finally:
        srv.shutdown()

    assert rc == _EXIT_OK
    out = capsys.readouterr().out
    result = json.loads(out)

    # dupNode mapped to the duplicate_of id with status linked_existing
    assert result["nodes"]["dupNode"]["id"] == dup_id
    assert result["nodes"]["dupNode"]["status"] == "linked_existing"

    # Edge was resolved using the duplicate_of id for source
    edge_posts = [
        r for r in _StubHandler.requests_received
        if r["method"] == "POST" and r["path"] == _edge_path()
    ]
    assert len(edge_posts) == 1
    assert edge_posts[0]["body"]["source_id"] == dup_id
    assert edge_posts[0]["body"]["target_id"] == context_id


# ---------------------------------------------------------------------------
# 3. Edge ref passthrough for unknown refs
# ---------------------------------------------------------------------------


def test_batch_add_edge_unknown_ref_passes_through(
    env_with_message_id: Any,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An edge source/target that is not a known ref is sent as-is to the server."""
    real_server_id = "realid000001"
    new_node_id = "newnode00001"
    edge_id = "edgeid000001"

    node_call_count: list[int] = [0]

    class _PassthroughHandler(_StubHandler):
        def _dispatch(self, method: str) -> None:  # type: ignore[override]
            body = self._read_body() if method == "POST" else {}
            _StubHandler.requests_received.append(
                {"method": method, "path": self.path, "body": body}
            )
            if method == "POST" and self.path == _node_path():
                self._respond(201, {"id": new_node_id, "claim": "x", "node_type": "context"})
            elif method == "POST" and self.path == _edge_path():
                self._respond(201, {"id": edge_id, "source_id": real_server_id, "target_id": new_node_id, "edge_type": "depends_on"})
            else:
                self._respond(404, {"error": "not found"})

    srv = http.server.HTTPServer(("127.0.0.1", 0), _PassthroughHandler)
    _StubHandler.requests_received = []
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    try:
        env_with_message_id(f"http://127.0.0.1:{port}")
        doc = {
            "nodes": [
                {"ref": "newNode", "type": "context", "claim": "New node"},
            ],
            "edges": [
                # real_server_id is NOT a ref — should be passed through as-is.
                {"source": real_server_id, "target": "newNode", "type": "depends_on", "rationale": "r"}
            ],
        }
        findings_file = _write_findings(tmp_path, doc)
        rc = main(["add", findings_file])
    finally:
        srv.shutdown()

    assert rc == _EXIT_OK
    edge_posts = [
        r for r in _StubHandler.requests_received
        if r["method"] == "POST" and r["path"] == _edge_path()
    ]
    assert len(edge_posts) == 1
    # Unknown ref passes through unchanged
    assert edge_posts[0]["body"]["source_id"] == real_server_id
    assert edge_posts[0]["body"]["target_id"] == new_node_id


# ---------------------------------------------------------------------------
# 4. All failed → exit 3
# ---------------------------------------------------------------------------


def test_batch_add_all_failed_exit_3(
    env_with_message_id: Any,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When all node POSTs fail with 422, exit code is 3."""

    class _FailHandler(_StubHandler):
        def _dispatch(self, method: str) -> None:  # type: ignore[override]
            body = self._read_body() if method == "POST" else {}
            _StubHandler.requests_received.append(
                {"method": method, "path": self.path, "body": body}
            )
            self._respond(422, {"detail": "invalid"})

    srv = http.server.HTTPServer(("127.0.0.1", 0), _FailHandler)
    _StubHandler.requests_received = []
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    try:
        env_with_message_id(f"http://127.0.0.1:{port}")
        doc = {
            "nodes": [{"ref": "a", "type": "context", "claim": "Will fail"}],
            "edges": [],
        }
        findings_file = _write_findings(tmp_path, doc)
        rc = main(["add", findings_file])
    finally:
        srv.shutdown()

    assert rc == _EXIT_SERVER


# ---------------------------------------------------------------------------
# 5. Partial success → exit 0
# ---------------------------------------------------------------------------


def test_batch_add_partial_success_exit_0(
    env_with_message_id: Any,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When at least one item succeeds, exit code is 0 even if others fail."""
    call_count: list[int] = [0]

    class _PartialHandler(_StubHandler):
        def _dispatch(self, method: str) -> None:  # type: ignore[override]
            body = self._read_body() if method == "POST" else {}
            _StubHandler.requests_received.append(
                {"method": method, "path": self.path, "body": body}
            )
            idx = call_count[0]
            call_count[0] += 1
            if idx == 0:
                self._respond(201, {"id": "ok000001", "claim": "x", "node_type": "context"})
            else:
                self._respond(422, {"detail": "invalid"})

    srv = http.server.HTTPServer(("127.0.0.1", 0), _PartialHandler)
    _StubHandler.requests_received = []
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    try:
        env_with_message_id(f"http://127.0.0.1:{port}")
        doc = {
            "nodes": [
                {"ref": "good", "type": "context", "claim": "OK"},
                {"ref": "bad", "type": "context", "claim": "Fails"},
            ],
            "edges": [],
        }
        findings_file = _write_findings(tmp_path, doc)
        rc = main(["add", findings_file])
    finally:
        srv.shutdown()

    assert rc == _EXIT_OK


# ---------------------------------------------------------------------------
# 6. add-node granular happy path
# ---------------------------------------------------------------------------


def test_add_node_granular_happy_path(
    env_with_message_id: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """add-node with valid args → prints {"node_id": ...}, exit 0."""
    node_id = _FAKE_NODE_ID
    base_url, srv = _make_server(
        {f"POST {_node_path()}": (201, {"id": node_id, "claim": "x", "node_type": "context"})}
    )
    try:
        env_with_message_id(base_url)
        rc = main([
            "add-node",
            "--claim", "GRJR beheert regionale jeugdhulp.",
            "--type", "context",
            "--confidence", "high",
        ])
    finally:
        srv.shutdown()

    assert rc == _EXIT_OK
    out = capsys.readouterr().out
    assert json.loads(out) == {"node_id": node_id}


# ---------------------------------------------------------------------------
# 7. add-node granular 409 → duplicate_of, exit 0
# ---------------------------------------------------------------------------


def test_add_node_granular_409(
    env_with_message_id: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """add-node 409 → {"duplicate_of": ...}, exit 0."""
    dup_id = _FAKE_DUPLICATE_ID
    base_url, srv = _make_server(
        {f"POST {_node_path()}": (409, {"detail": {"duplicate_of": dup_id}})}
    )
    try:
        env_with_message_id(base_url)
        rc = main([
            "add-node",
            "--claim", "Duplicate claim.",
            "--type", "context",
            "--confidence", "medium",
        ])
    finally:
        srv.shutdown()

    assert rc == _EXIT_OK
    out = capsys.readouterr().out
    assert json.loads(out) == {"duplicate_of": dup_id}


# ---------------------------------------------------------------------------
# 8. add-edge granular happy path
# ---------------------------------------------------------------------------


def test_add_edge_granular_happy_path(
    env_with_message_id: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """add-edge with valid args → edge JSON, exit 0."""
    edge_body = {
        "id": _FAKE_EDGE_ID,
        "source_id": "src001",
        "target_id": "tgt002",
        "edge_type": "causes",
    }
    base_url, srv = _make_server(
        {f"POST {_edge_path()}": (201, edge_body)}
    )
    try:
        env_with_message_id(base_url)
        rc = main([
            "add-edge",
            "--from", "src001",
            "--to", "tgt002",
            "--type", "causes",
            "--rationale", "Without GRJR intervention costs rise 15% (diff-in-diff vs peers).",
        ])
    finally:
        srv.shutdown()

    assert rc == _EXIT_OK
    out = capsys.readouterr().out
    assert json.loads(out)["id"] == _FAKE_EDGE_ID


# ---------------------------------------------------------------------------
# 9. set-summary → 204, exit 0
# ---------------------------------------------------------------------------


def test_set_summary(
    env_with_message_id: Any,
) -> None:
    """set-summary → 204, exit 0."""
    base_url, srv = _make_server(
        {f"POST {_summary_path()}": (204, None)}
    )
    try:
        env_with_message_id(base_url)
        rc = main(["set-summary", "--text", "Phase 1 complete: 4 context nodes added."])
    finally:
        srv.shutdown()

    assert rc == _EXIT_OK


# ---------------------------------------------------------------------------
# 10. list --compact → JSON to stdout, exit 0
# ---------------------------------------------------------------------------


def test_list_compact(
    env_with_message_id: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """list --compact → server JSON printed to stdout, exit 0."""
    compact_body = {
        "nodes": [{"id": "abc", "claim": "Claim", "node_type": "context"}],
        "edges": [],
    }
    base_url, srv = _make_server(
        {f"GET {_graph_compact_path()}": (200, compact_body)}
    )
    try:
        env_with_message_id(base_url)
        rc = main(["list", "--compact"])
    finally:
        srv.shutdown()

    assert rc == _EXIT_OK
    out = capsys.readouterr().out
    assert json.loads(out) == compact_body


# ---------------------------------------------------------------------------
# 11. Server unreachable → exit 3
# ---------------------------------------------------------------------------


def test_server_unreachable_exit_3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the platform is not running, exit code is 3."""
    monkeypatch.setenv("CAPELLE_MESSAGE_ID", "msg-test-001")
    # Use a port that is almost certainly not bound.
    monkeypatch.setenv("CAPELLE_INTERNAL_URL", "http://127.0.0.1:19999")

    rc = main(["add-node", "--claim", "Test.", "--type", "context", "--confidence", "medium"])
    assert rc == _EXIT_SERVER


# ---------------------------------------------------------------------------
# 12. CAPELLE_MESSAGE_ID missing → exit 2
# ---------------------------------------------------------------------------


def test_missing_message_id_exit_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing CAPELLE_MESSAGE_ID causes exit 2."""
    monkeypatch.delenv("CAPELLE_MESSAGE_ID", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        main(["add-node", "--claim", "x", "--type", "context", "--confidence", "medium"])
    assert exc_info.value.code == _EXIT_USAGE


# ---------------------------------------------------------------------------
# 13. Batch file not found → exit 2
# ---------------------------------------------------------------------------


def test_batch_file_not_found_exit_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the findings file does not exist, exit 2."""
    monkeypatch.setenv("CAPELLE_MESSAGE_ID", "msg-test-001")
    monkeypatch.setenv("CAPELLE_INTERNAL_URL", "http://127.0.0.1:8080")
    rc = main(["add", "/nonexistent/path/findings.json"])
    assert rc == _EXIT_USAGE


# ---------------------------------------------------------------------------
# 14. Graph feature disabled → exit 3
# ---------------------------------------------------------------------------


def test_graph_feature_disabled_exit_3(
    env_with_message_id: Any,
) -> None:
    """When the server returns 404 with 'disabled', exit 3."""
    base_url, srv = _make_server(
        {f"POST {_node_path()}": (404, {"error": "graph feature disabled"})}
    )
    try:
        env_with_message_id(base_url)
        rc = main([
            "add-node",
            "--claim", "Test.",
            "--type", "context",
            "--confidence", "medium",
        ])
    finally:
        srv.shutdown()

    assert rc == _EXIT_SERVER
