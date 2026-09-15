"""
Tests for the elicitation package and its HTTP endpoints.

Covers:

1. ``InMemoryElicitationRegistry`` unit tests:
   - register → resolve sets the Future result.
   - resolve on an unknown question_id → returns False.
   - discard removes the entry (subsequent resolve → False).

2. ``InternalHandler.ask`` endpoint:
   - Non-localhost client → 403.
   - Unknown message_id → 404 JSON.

3. Integration-style test:
   - Register a question via the handler, resolve it via the registry
     directly, confirm the awaiting ``ask`` coroutine returns the answer.

4. Timeout path:
   - Monkeypatch ``elicitation_timeout_seconds`` to a very small value;
     confirm the response includes ``timed_out: true``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from capelle_platform.elicitation.impl_memory import InMemoryElicitationRegistry
from capelle_platform.settings import Settings

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Registry unit tests
# ---------------------------------------------------------------------------


_OWNER = "user-owner"


async def test_register_and_resolve_sets_future_result() -> None:
    """Registering a question and then resolving it completes the Future."""
    registry = InMemoryElicitationRegistry()
    future = await registry.register("q-001", _OWNER)

    assert not future.done(), "Future should be pending before resolve"

    resolved = registry.resolve("q-001", "yes please", _OWNER)

    assert resolved is True
    assert future.done()
    assert future.result() == "yes please"


async def test_resolve_unknown_returns_false() -> None:
    """Resolving an ID that was never registered returns False."""
    registry = InMemoryElicitationRegistry()
    result = registry.resolve("does-not-exist", "irrelevant", _OWNER)
    assert result is False


async def test_resolve_wrong_owner_returns_false() -> None:
    """A different user resolving an owned question is rejected (API-1)."""
    registry = InMemoryElicitationRegistry()
    future = await registry.register("q-owned", _OWNER)

    # An attacker who guessed the question_id but is a different user.
    assert registry.resolve("q-owned", "injected", "user-attacker") is False
    assert not future.done(), "Future must remain pending after a rejected resolve"

    # The legitimate owner still succeeds.
    assert registry.resolve("q-owned", "legit", _OWNER) is True
    assert future.result() == "legit"


async def test_discard_removes_entry() -> None:
    """After discard, resolving the same ID returns False."""
    registry = InMemoryElicitationRegistry()
    await registry.register("q-002", _OWNER)
    registry.discard("q-002")

    result = registry.resolve("q-002", "too late", _OWNER)
    assert result is False


async def test_discard_idempotent_on_absent_id() -> None:
    """discard on an absent ID must not raise."""
    registry = InMemoryElicitationRegistry()
    registry.discard("never-registered")  # should not raise


async def test_resolve_already_done_returns_false() -> None:
    """Resolving a Future that is already done returns False."""
    registry = InMemoryElicitationRegistry()
    future = await registry.register("q-003", _OWNER)
    # First resolve succeeds.
    assert registry.resolve("q-003", "first", _OWNER) is True
    # Second resolve on the same (now-done) Future → False.
    assert registry.resolve("q-003", "second", _OWNER) is False
    # Result is still the first answer.
    assert future.result() == "first"


# ---------------------------------------------------------------------------
# InternalHandler.ask endpoint — non-localhost guard
# ---------------------------------------------------------------------------


def _make_fake_request(
    *,
    client_host: str,
    body: dict[str, Any],
    session_id: str = "sess-1",
) -> MagicMock:
    """Build a minimal fake ``fastapi.Request``."""
    import json as _json

    req = MagicMock()
    req.client = MagicMock()
    req.client.host = client_host
    req.json = AsyncMock(return_value=body)
    req.body = AsyncMock(return_value=_json.dumps(body).encode("utf-8"))
    req.headers = {}
    return req


async def _make_handler(
    *,
    registry: InMemoryElicitationRegistry | None = None,
    message_user_map: dict[str, str] | None = None,
    timeout: int = 300,
) -> "Any":
    """
    Build an ``InternalHandler`` with a fake store + ws_hub.

    ``message_user_map`` maps message_id → user_id for
    ``_resolve_user_id``.  Unknown keys resolve to None.
    """
    from capelle_platform.handlers.internal import InternalHandler

    # Fake store: get_message and get_session wire the resolution chain.
    user_map: dict[str, str] = message_user_map or {}

    store = MagicMock()

    async def _get_message(mid: str):  # type: ignore[no-untyped-def]
        if mid not in user_map:
            return None
        msg = MagicMock()
        msg.session_id = f"sess-{mid}"
        return msg

    async def _get_session(sid: str):  # type: ignore[no-untyped-def]
        # Extract message_id back from the session_id convention above.
        mid = sid.removeprefix("sess-")
        if mid not in user_map:
            return None
        sess = MagicMock()
        sess.user_id = user_map[mid]
        return sess

    store.get_message = _get_message
    store.get_session = _get_session

    ws_hub = MagicMock()
    ws_hub.broadcast_to_user = AsyncMock()

    settings_obj = MagicMock(spec=Settings)
    settings_obj.elicitation_timeout_seconds = timeout
    settings_obj.internal_hook_secret = ""  # HMAC disabled in these unit tests

    handler = InternalHandler(
        ws_hub=ws_hub,
        store=store,
        registry=registry,
        settings=settings_obj,
    )
    return handler, ws_hub


async def test_ask_rejects_non_localhost() -> None:
    """Non-localhost client receives 403 JSON with error='forbidden'."""
    handler, _ = await _make_handler()
    request = _make_fake_request(client_host="1.2.3.4", body={"question": "hi?"})

    response = await handler.ask("msg-abc", request)

    assert response.status_code == 403
    import json
    data = json.loads(response.body)
    assert data["answer"] == ""
    assert data["error"] == "forbidden"


async def test_ask_returns_404_for_unknown_message() -> None:
    """Unknown message_id → 404 with error='unknown message'."""
    registry = InMemoryElicitationRegistry()
    handler, _ = await _make_handler(
        registry=registry,
        message_user_map={},  # no known messages
    )
    request = _make_fake_request(
        client_host="127.0.0.1",
        body={"question": "what should I do?"},
    )

    response = await handler.ask("unknown-msg-id", request)

    assert response.status_code == 404
    import json
    data = json.loads(response.body)
    assert data["error"] == "unknown message"


# ---------------------------------------------------------------------------
# Integration: ask → answer round-trip
# ---------------------------------------------------------------------------


async def test_ask_returns_answer_when_resolved() -> None:
    """
    Full round-trip: the ``ask`` coroutine suspends on the Future; the
    registry is resolved externally; ``ask`` returns the answer.
    """
    registry = InMemoryElicitationRegistry()
    handler, ws_hub = await _make_handler(
        registry=registry,
        message_user_map={"msg-42": "user-99"},
        timeout=5,  # short but not instant — gives us time to resolve
    )
    request = _make_fake_request(
        client_host="127.0.0.1",
        body={"question": "Proceed?", "options": ["yes", "no"], "allow_free_text": False},
    )

    # Capture the question_id from the broadcast so we can resolve it.
    captured_qid: list[str] = []

    async def _capture_broadcast(user_id: str, event: dict[str, Any]) -> None:
        captured_qid.append(event["question_id"])

    ws_hub.broadcast_to_user.side_effect = _capture_broadcast

    # Launch ask concurrently.
    ask_task = asyncio.create_task(handler.ask("msg-42", request))

    # Yield control so ``ask`` runs up to ``await asyncio.wait_for``.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # By now broadcast_to_user should have been called and question_id captured.
    assert captured_qid, "broadcast_to_user was not called"
    question_id = captured_qid[0]

    # Resolve via the registry (simulating the browser POST to /api/analyses/answer).
    # The owning user is "user-99" (message_user_map above).
    resolved = registry.resolve(question_id, "yes", "user-99")
    assert resolved is True

    response = await ask_task

    import json
    data = json.loads(response.body)
    assert data["answer"] == "yes"
    assert data["question_id"] == question_id
    assert "timed_out" not in data


# ---------------------------------------------------------------------------
# Timeout path
# ---------------------------------------------------------------------------


async def test_ask_returns_timed_out_on_timeout() -> None:
    """When no answer arrives within the timeout, ``timed_out: true`` is returned."""
    registry = InMemoryElicitationRegistry()
    handler, _ = await _make_handler(
        registry=registry,
        message_user_map={"msg-to": "user-1"},
        timeout=0,  # expire immediately
    )
    request = _make_fake_request(
        client_host="127.0.0.1",
        body={"question": "Answer quickly?"},
    )

    response = await handler.ask("msg-to", request)

    import json
    data = json.loads(response.body)
    assert data["answer"] == ""
    assert data.get("timed_out") is True
    assert "question_id" in data

    # After timeout the entry must have been discarded.
    assert registry.resolve(data["question_id"], "late", "user-1") is False


# ---------------------------------------------------------------------------
# AnalysisHandler.submit_answer
# ---------------------------------------------------------------------------


def _make_analysis_handler(
    registry: InMemoryElicitationRegistry | None,
) -> "Any":
    """Build an AnalysisHandler with fakes for deps we don't need here."""
    from capelle_platform.handlers.analysis import AnalysisHandler

    store = MagicMock()
    publisher = MagicMock()

    authenticator = MagicMock()
    fake_user = MagicMock()
    fake_user.id = "user-1"
    authenticator.validate_token = MagicMock(return_value=fake_user)

    settings = MagicMock()
    settings.auth_cookie_name = "capelle_auth"
    handler = AnalysisHandler(
        store=store,
        publisher=publisher,
        authenticator=authenticator,
        settings=settings,
        registry=registry,
    )
    return handler


def _make_authed_request(body: dict[str, Any]) -> MagicMock:
    """Fake authenticated request for AnalysisHandler (Bearer path)."""
    req = MagicMock()
    req.cookies = {}  # no cookie -> falls through to the Bearer header
    req.headers = {"Authorization": "Bearer test-token"}
    req.json = AsyncMock(return_value=body)
    return req


async def test_submit_answer_resolves_pending_question() -> None:
    """submit_answer delivers the answer to a registered Future."""
    registry = InMemoryElicitationRegistry()
    # The fake authenticated user in _make_analysis_handler is "user-1".
    future = await registry.register("qid-x", "user-1")

    handler = _make_analysis_handler(registry)
    request = _make_authed_request({"question_id": "qid-x", "answer": "blue"})

    result = await handler.submit_answer(request)

    assert result["resolved"] is True
    assert future.result() == "blue"


async def test_submit_answer_rejects_other_users_question() -> None:
    """A logged-in user cannot answer another user's question (API-1)."""
    registry = InMemoryElicitationRegistry()
    future = await registry.register("qid-victim", "victim-user")

    # handler's authenticated user is "user-1" (the attacker here).
    handler = _make_analysis_handler(registry)
    request = _make_authed_request(
        {"question_id": "qid-victim", "answer": "injected"}
    )

    result = await handler.submit_answer(request)

    assert result["resolved"] is False
    assert not future.done(), "victim's question must remain unanswered"


async def test_submit_answer_unknown_question_returns_false() -> None:
    """submit_answer with an unknown question_id returns resolved=false."""
    registry = InMemoryElicitationRegistry()
    handler = _make_analysis_handler(registry)
    request = _make_authed_request({"question_id": "no-such-id", "answer": "x"})

    result = await handler.submit_answer(request)
    assert result["resolved"] is False


# ---------------------------------------------------------------------------
# Per-mode elicitation window
# ---------------------------------------------------------------------------


async def _make_handler_with_mode(
    *,
    mode: str,
    registry: InMemoryElicitationRegistry | None = None,
) -> "Any":
    """
    Build an InternalHandler whose fake session carries the given mode.

    The store resolves message_id → session.mode so the handler can pick
    the right per-mode elicitation timeout.
    """
    from unittest.mock import AsyncMock, MagicMock
    from capelle_platform.handlers.internal import InternalHandler

    user_map: dict[str, str] = {"msg-mode": "user-mode"}

    store = MagicMock()

    async def _get_message(mid: str):  # type: ignore[no-untyped-def]
        if mid not in user_map:
            return None
        msg = MagicMock()
        msg.session_id = f"sess-{mid}"
        return msg

    async def _get_session(sid: str):  # type: ignore[no-untyped-def]
        mid = sid.removeprefix("sess-")
        if mid not in user_map:
            return None
        sess = MagicMock()
        sess.user_id = user_map[mid]
        sess.mode = mode
        return sess

    store.get_message = _get_message
    store.get_session = _get_session

    ws_hub = MagicMock()
    ws_hub.broadcast_to_user = AsyncMock()

    # Use a real Settings so ModeConfigFactory.from_mode works correctly.
    settings_obj = Settings(
        jwt_secret="x" * 32,
        job_timeout_seconds=70,
        elicitation_timeout_seconds=5,
    )

    handler = InternalHandler(
        ws_hub=ws_hub,
        store=store,
        registry=registry,
        settings=settings_obj,
    )
    return handler, ws_hub


@pytest.mark.parametrize("mode,expected_timeout", [
    ("groeikern", 600.0),
    ("jeugdzorg", 300.0),
])
async def test_ask_uses_per_mode_elicitation_timeout(
    monkeypatch, mode: str, expected_timeout: float
) -> None:
    """
    /ask must use the per-mode elicitation window, not the global default.

    We monkeypatch asyncio.wait_for to capture the timeout kwarg, then
    confirm the handler resolved the correct per-mode value.
    """
    import asyncio as _asyncio

    registry = InMemoryElicitationRegistry()
    handler, _ = await _make_handler_with_mode(mode=mode, registry=registry)

    captured_timeout: list[float] = []

    _original_wait_for = _asyncio.wait_for

    async def _fake_wait_for(coro, *, timeout, **kwargs):  # type: ignore[no-untyped-def]
        captured_timeout.append(float(timeout))
        # Cancel immediately so the test doesn't actually wait.
        raise _asyncio.TimeoutError()

    import capelle_platform.handlers.internal as _internal_mod
    monkeypatch.setattr(_internal_mod.asyncio, "wait_for", _fake_wait_for)

    request = _make_fake_request(
        client_host="127.0.0.1",
        body={"question": "Which mode am I?"},
    )

    response = await handler.ask("msg-mode", request)
    # Timed out is expected — we forced it.
    import json
    data = json.loads(response.body)
    assert data.get("timed_out") is True

    assert captured_timeout, "wait_for was never called"
    assert captured_timeout[0] == expected_timeout, (
        f"Expected elicitation timeout {expected_timeout}s for mode={mode!r}, "
        f"got {captured_timeout[0]}s"
    )


async def test_submit_answer_requires_auth() -> None:
    """Unauthenticated request raises 401."""
    from fastapi import HTTPException

    registry = InMemoryElicitationRegistry()
    handler = _make_analysis_handler(registry)

    # Override authenticator to raise on invalid token.
    handler._auth.validate_token.side_effect = ValueError("bad token")

    req = MagicMock()
    req.headers = {"Authorization": "Bearer bad"}
    req.json = AsyncMock(return_value={"question_id": "q", "answer": "a"})

    with pytest.raises(HTTPException) as exc_info:
        await handler.submit_answer(req)

    assert exc_info.value.status_code == 401


async def test_submit_answer_accepts_the_auth_cookie() -> None:
    """Regression: the web frontend authenticates with the HttpOnly cookie
    (no Bearer header). submit_answer must accept it — it 401'd before because
    AnalysisHandler only read the Authorization header."""
    registry = InMemoryElicitationRegistry()
    fut = await registry.register("qid-cookie", "user-1")
    handler = _make_analysis_handler(registry)  # settings.auth_cookie_name = "capelle_auth"

    req = MagicMock()
    req.cookies = {"capelle_auth": "valid-jwt"}  # cookie only, NO Bearer header
    req.headers = {}
    req.json = AsyncMock(return_value={"question_id": "qid-cookie", "answer": "blauw"})

    result = await handler.submit_answer(req)
    assert result == {"resolved": True}
    assert fut.done() and fut.result() == "blauw"
