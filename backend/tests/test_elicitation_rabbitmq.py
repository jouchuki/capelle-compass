"""Tests for the cross-instance RabbitMQ elicitation registry.

These exercise the local-resolve, ownership, routing, and consumer-apply
logic without a live broker (start() is never called).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from capelle_platform.elicitation.impl_rabbitmq import RabbitMQElicitationRegistry
from capelle_platform.settings import Settings


def _settings(monkeypatch) -> Settings:
    monkeypatch.setenv("CAPELLE_JWT_SECRET", "x" * 48)
    return Settings()


class _FakeMessage:
    """Minimal stand-in for an aio-pika incoming message."""

    def __init__(self, body: bytes) -> None:
        self.body = body

    async def ack(self) -> None:  # noqa: D401 — test stub
        return None


@pytest.mark.asyncio
async def test_local_resolve_sets_future(monkeypatch):
    reg = RabbitMQElicitationRegistry(_settings(monkeypatch))
    fut = await reg.register("q1", "userA")
    assert reg.resolve("q1", "the answer", "userA") is True
    assert await asyncio.wait_for(fut, 1) == "the answer"


@pytest.mark.asyncio
async def test_owner_mismatch_rejected(monkeypatch):
    reg = RabbitMQElicitationRegistry(_settings(monkeypatch))
    fut = await reg.register("q2", "userA")
    assert reg.resolve("q2", "x", "attacker") is False
    assert not fut.done()


@pytest.mark.asyncio
async def test_unknown_qid_routes_without_raising(monkeypatch):
    # A question not held locally is assumed to live on another yuta:
    # resolve() returns True (routed via fanout) and must not raise even
    # though the broker was never started (exchange is None).
    reg = RabbitMQElicitationRegistry(_settings(monkeypatch))
    assert reg.resolve("remote-q", "ans", "userA") is True
    await asyncio.sleep(0)  # let the fire-and-forget publish task no-op


@pytest.mark.asyncio
async def test_consumer_resolves_local_future(monkeypatch):
    # Simulates a fanned-out answer arriving for a question we own.
    reg = RabbitMQElicitationRegistry(_settings(monkeypatch))
    fut = await reg.register("q3", "userA")
    body = json.dumps(
        {"question_id": "q3", "answer": "remote answer", "requesting_user_id": "userA"}
    ).encode("utf-8")
    await reg._on_broker_message(_FakeMessage(body))
    assert await asyncio.wait_for(fut, 1) == "remote answer"


@pytest.mark.asyncio
async def test_consumer_ignores_unknown_and_bad_payloads(monkeypatch):
    reg = RabbitMQElicitationRegistry(_settings(monkeypatch))
    # Unknown qid -> no-op, no raise.
    await reg._on_broker_message(
        _FakeMessage(json.dumps({"question_id": "nope", "answer": "x", "requesting_user_id": "u"}).encode())
    )
    # Malformed JSON -> swallowed.
    await reg._on_broker_message(_FakeMessage(b"not json"))


@pytest.mark.asyncio
async def test_discard_removes_local_entry(monkeypatch):
    reg = RabbitMQElicitationRegistry(_settings(monkeypatch))
    await reg.register("q4", "userA")
    reg.discard("q4")
    assert "q4" not in reg._pending
    reg.discard("q4")  # idempotent
