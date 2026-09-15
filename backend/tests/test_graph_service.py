"""
Unit tests for GraphService — dedup guard, orchestration, broadcast.

TDD: written FIRST (red), then green once service.py is implemented.

Covers:
1. add_node happy path → node persisted, broadcast called with graph_delta.
2. add_node dedup guard → near-identical claim raises DuplicateNodeError.
3. add_node with a completely different claim → no dedup collision.
4. add_node normalization: punctuation / case differences below DEDUP_RATIO
   do NOT trigger dedup; above it they do.
5. add_edge happy path → edge persisted, broadcast called.
6. add_edge unknown source_id → raises UnknownNodeError.
7. add_edge unknown target_id → raises UnknownNodeError.
8. set_summary → store updated, broadcast called.
9. get_compact → returns (id, claim, node_type) per node + edge triples.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from capelle_platform.graph.impl_memory import InMemoryGraphStore
from capelle_platform.graph.models import (
    Confidence,
    EdgeType,
    FindingGraph,
    GraphEdge,
    GraphNode,
    NodeStatus,
    NodeType,
)
from capelle_platform.utils import generate_id

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION = "sess-unit-test"


def _make_context_node(claim: str = "Amsterdam regelt regionaal inkoop via GRJR.") -> GraphNode:
    """Build a context node (no evidence required)."""
    return GraphNode(
        id=generate_id(),
        node_type=NodeType.CONTEXT,
        claim=claim,
        confidence=Confidence.MEDIUM,
    )


def _make_finding_node(claim: str = "Capelle besteedt meer aan jeugdzorg dan peers.") -> GraphNode:
    """Build a finding node with minimal evidence."""
    return GraphNode(
        id=generate_id(),
        node_type=NodeType.FINDING,
        claim=claim,
        confidence=Confidence.HIGH,
        blocks=[{"type": "text", "text": "CBS data 2024."}],
    )


async def _make_service():
    """Build a GraphService backed by an in-memory store + mock broadcaster."""
    from capelle_platform.graph.service import GraphService

    store = InMemoryGraphStore()
    await store.initialize()
    broadcast = AsyncMock()
    service = GraphService(store=store, broadcast=broadcast)
    return service, store, broadcast


# ---------------------------------------------------------------------------
# add_node — happy path
# ---------------------------------------------------------------------------


async def test_add_node_happy_path_returns_node() -> None:
    """add_node returns the persisted GraphNode."""
    service, store, broadcast = await _make_service()
    node = _make_context_node()

    result = await service.add_node(_SESSION, "msg-1", node)

    assert result.id == node.id
    assert result.claim == node.claim


async def test_add_node_persists_to_store() -> None:
    """add_node stores the node so get() reflects it."""
    service, store, broadcast = await _make_service()
    node = _make_context_node()

    await service.add_node(_SESSION, "msg-1", node)

    graph = await store.get(_SESSION)
    assert graph is not None
    assert any(n.id == node.id for n in graph.nodes)


async def test_add_node_broadcasts_graph_delta() -> None:
    """add_node calls broadcast with {type:graph_delta, kind:node, data:node}."""
    service, store, broadcast = await _make_service()
    node = _make_context_node()

    await service.add_node(_SESSION, "msg-42", node)

    broadcast.assert_awaited_once()
    event = broadcast.call_args[0][0]
    assert event["type"] == "graph_delta"
    assert event["session_id"] == _SESSION
    assert event["message_id"] == "msg-42"
    assert event["kind"] == "node"
    assert event["data"]["id"] == node.id


# ---------------------------------------------------------------------------
# add_node — dedup guard
# ---------------------------------------------------------------------------


async def test_add_node_dedup_raises_for_near_identical_claim() -> None:
    """A near-identical claim (ratio >= DEDUP_RATIO) raises DuplicateNodeError."""
    from capelle_platform.graph.service import DuplicateNodeError

    service, store, broadcast = await _make_service()
    original = _make_context_node("De gemeente Capelle groeit harder dan de regio.")
    await service.add_node(_SESSION, "msg-1", original)

    # Whitespace / minor variation — should still hit the dedup threshold.
    near_dup = _make_context_node("De gemeente Capelle groeit harder dan de regio")
    with pytest.raises(DuplicateNodeError) as exc_info:
        await service.add_node(_SESSION, "msg-2", near_dup)

    assert exc_info.value.existing_id == original.id


async def test_add_node_dedup_exposes_existing_id() -> None:
    """DuplicateNodeError.existing_id points to the first-stored node."""
    from capelle_platform.graph.service import DuplicateNodeError

    service, _, _ = await _make_service()
    first = _make_context_node("Capelle aan den IJssel is een groeikern gemeente.")
    await service.add_node(_SESSION, "msg-1", first)

    dup = _make_context_node("Capelle aan den IJssel is een groeikern gemeente")
    with pytest.raises(DuplicateNodeError) as exc_info:
        await service.add_node(_SESSION, "msg-2", dup)

    assert exc_info.value.existing_id == first.id


async def test_add_node_no_dedup_for_different_claim() -> None:
    """A clearly different claim does not trigger dedup."""
    service, store, broadcast = await _make_service()
    node_a = _make_context_node("De gemeente Capelle groeit harder dan de regio.")
    node_b = _make_context_node("GRJR regelt de regionale jeugdhulp inkoop.")

    await service.add_node(_SESSION, "msg-1", node_a)
    # Must not raise:
    result = await service.add_node(_SESSION, "msg-2", node_b)

    assert result.id == node_b.id
    graph = await store.get(_SESSION)
    assert graph is not None
    assert len(graph.nodes) == 2


async def test_dedup_ignores_pruned_nodes() -> None:
    """Pruned nodes are not candidates for dedup comparison."""
    from capelle_platform.graph.service import DuplicateNodeError

    service, store, broadcast = await _make_service()
    original = _make_context_node("De gemeente Capelle groeit sneller dan peers.")
    await service.add_node(_SESSION, "msg-1", original)
    await store.prune_node(_SESSION, original.id)

    # Same claim but original is pruned — should NOT raise DuplicateNodeError.
    near_dup = _make_context_node("De gemeente Capelle groeit sneller dan peers")
    result = await service.add_node(_SESSION, "msg-2", near_dup)
    assert result.id == near_dup.id


# ---------------------------------------------------------------------------
# add_edge — happy path
# ---------------------------------------------------------------------------


async def test_add_edge_happy_path_returns_edge() -> None:
    """add_edge returns the persisted GraphEdge when both endpoints exist."""
    service, store, broadcast = await _make_service()
    source = _make_context_node("GRJR beheert regionale jeugdhulp.")
    target = _make_context_node("Capelle betaalt via GRJR contributie.")

    await service.add_node(_SESSION, "msg-1", source)
    await service.add_node(_SESSION, "msg-1", target)

    edge = GraphEdge(
        id=generate_id(),
        source_id=source.id,
        target_id=target.id,
        edge_type=EdgeType.CONTROLS,
        rationale="GRJR bepaalt de inkoopvoorwaarden.",
    )
    result = await service.add_edge(_SESSION, "msg-1", edge)
    assert result.id == edge.id


async def test_add_edge_broadcasts_graph_delta() -> None:
    """add_edge calls broadcast with {type:graph_delta, kind:edge}."""
    service, store, broadcast = await _make_service()
    source = _make_context_node("Bron.")
    target = _make_context_node("Doel.")
    await service.add_node(_SESSION, "msg-1", source)
    broadcast.reset_mock()
    await service.add_node(_SESSION, "msg-1", target)
    broadcast.reset_mock()

    edge = GraphEdge(
        id=generate_id(),
        source_id=source.id,
        target_id=target.id,
        edge_type=EdgeType.EXPLAINS,
        rationale="Verklaart het verband.",
    )
    await service.add_edge(_SESSION, "msg-1", edge)

    broadcast.assert_awaited_once()
    event = broadcast.call_args[0][0]
    assert event["type"] == "graph_delta"
    assert event["kind"] == "edge"
    assert event["data"]["id"] == edge.id


# ---------------------------------------------------------------------------
# add_edge — unknown endpoint guard
# ---------------------------------------------------------------------------


async def test_add_edge_rejects_unknown_source_id() -> None:
    """add_edge raises UnknownNodeError when source_id is not in the graph."""
    from capelle_platform.graph.service import UnknownNodeError

    service, store, broadcast = await _make_service()
    target = _make_context_node("Bestaand doel-knooppunt.")
    await service.add_node(_SESSION, "msg-1", target)

    edge = GraphEdge(
        id=generate_id(),
        source_id="does-not-exist",
        target_id=target.id,
        edge_type=EdgeType.EXPLAINS,
    )
    with pytest.raises(UnknownNodeError) as exc_info:
        await service.add_edge(_SESSION, "msg-1", edge)
    assert exc_info.value.node_id == "does-not-exist"


async def test_add_edge_rejects_unknown_target_id() -> None:
    """add_edge raises UnknownNodeError when target_id is not in the graph."""
    from capelle_platform.graph.service import UnknownNodeError

    service, store, broadcast = await _make_service()
    source = _make_context_node("Bestaand bron-knooppunt.")
    await service.add_node(_SESSION, "msg-1", source)

    edge = GraphEdge(
        id=generate_id(),
        source_id=source.id,
        target_id="ghost-node",
        edge_type=EdgeType.EXPLAINS,
    )
    with pytest.raises(UnknownNodeError) as exc_info:
        await service.add_edge(_SESSION, "msg-1", edge)
    assert exc_info.value.node_id == "ghost-node"


# ---------------------------------------------------------------------------
# set_summary
# ---------------------------------------------------------------------------


async def test_set_summary_updates_store() -> None:
    """set_summary persists the summary text."""
    service, store, broadcast = await _make_service()
    await service.set_summary(_SESSION, "msg-s", "Fase 1: context verzameld.")

    graph = await store.get(_SESSION)
    assert graph is not None
    assert graph.summary == "Fase 1: context verzameld."


async def test_set_summary_broadcasts_graph_delta() -> None:
    """set_summary calls broadcast with {kind:summary, data:<text>}."""
    service, store, broadcast = await _make_service()
    await service.set_summary(_SESSION, "msg-s", "Korte samenvatting.")

    broadcast.assert_awaited_once()
    event = broadcast.call_args[0][0]
    assert event["type"] == "graph_delta"
    assert event["kind"] == "summary"
    assert event["data"] == "Korte samenvatting."


# ---------------------------------------------------------------------------
# get / get_compact
# ---------------------------------------------------------------------------


async def test_get_returns_none_for_empty_session() -> None:
    """get returns None when no graph exists for the session."""
    service, _, _ = await _make_service()
    result = await service.get("nonexistent-session")
    assert result is None


async def test_get_compact_returns_expected_shape() -> None:
    """get_compact returns id+claim+node_type per node + (source,target,type) triples."""
    service, store, broadcast = await _make_service()

    node_a = _make_context_node("Eerste knooppunt.")
    node_b = _make_context_node("Tweede knooppunt.")
    await service.add_node(_SESSION, "msg-1", node_a)
    await service.add_node(_SESSION, "msg-1", node_b)

    edge = GraphEdge(
        id=generate_id(),
        source_id=node_a.id,
        target_id=node_b.id,
        edge_type=EdgeType.DEPENDS_ON,
    )
    await service.add_edge(_SESSION, "msg-1", edge)

    compact = await service.get_compact(_SESSION)
    assert compact is not None

    assert len(compact["nodes"]) == 2
    first_node = compact["nodes"][0]
    assert set(first_node.keys()) == {"id", "claim", "node_type"}

    assert len(compact["edges"]) == 1
    edge_triple = compact["edges"][0]
    assert len(edge_triple) == 3  # (source_id, target_id, edge_type)
    assert edge_triple == (node_a.id, node_b.id, EdgeType.DEPENDS_ON.value)


async def test_get_compact_returns_none_for_empty_session() -> None:
    """get_compact returns None when no graph exists."""
    service, _, _ = await _make_service()
    result = await service.get_compact("empty-session")
    assert result is None


@pytest.mark.asyncio
async def test_add_edge_rejects_when_graph_is_empty() -> None:
    """add_edge into a session with NO graph at all raises UnknownNodeError."""
    from capelle_platform.graph.service import UnknownNodeError

    service, store, broadcast = await _make_service()
    edge = GraphEdge(
        id=generate_id(),
        source_id="ghost0000001",
        target_id="ghost0000002",
        edge_type=EdgeType.EXPLAINS,
    )
    with pytest.raises(UnknownNodeError):
        await service.add_edge("session-with-no-graph", "msg-x", edge)
    broadcast.assert_not_awaited()
