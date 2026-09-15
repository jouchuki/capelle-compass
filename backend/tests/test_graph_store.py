"""
Store round-trip tests for the finding-graph domain layer.

TDD: these tests are written FIRST and must fail until the implementation
is in place. They cover:
  - InMemoryGraphStore: full add/get/edge/summary/prune round-trips
  - Per-session limits (nodes, edges) enforced in one place
  - Version bump on every mutation
  - PostgresGraphStore: structural (SQL-string) test only — no live DB
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
import pytest_asyncio

from capelle_platform.graph.constants import (
    MAX_NODES_PER_SESSION,
    MAX_EDGES_PER_SESSION,
)
from capelle_platform.graph.models import (
    NodeType,
    EdgeType,
    Confidence,
    NodeStatus,
    GraphNode,
    GraphEdge,
)
from capelle_platform.graph.base_store import BaseGraphStore
from capelle_platform.graph.impl_memory import InMemoryGraphStore

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def store() -> AsyncIterator[BaseGraphStore]:
    """Yield a fresh in-memory store."""
    s = InMemoryGraphStore()
    yield s


def _make_finding_node(
    node_id: str = "abc123def456",
    message_id: str = "msg001",
) -> GraphNode:
    return GraphNode(
        id=node_id,
        node_type=NodeType.FINDING,
        claim="Capelle spends 20% more per case than peer cities.",
        blocks=[{"type": "text", "text": "CBS 2025 evidence."}],
        citations=[],
        confidence=Confidence.MEDIUM,
        status=NodeStatus.PROPOSED,
        agent_label="lead",
        message_id=message_id,
    )


def _make_context_node(
    node_id: str = "ctx000000001",
    message_id: str = "msg001",
) -> GraphNode:
    return GraphNode(
        id=node_id,
        node_type=NodeType.CONTEXT,
        claim="GRJR controls regional youth-care procurement for Capelle.",
        blocks=[],
        citations=[],
        confidence=Confidence.HIGH,
        status=NodeStatus.SUPPORTED,
        agent_label="lead",
        message_id=message_id,
    )


def _make_edge(source_id: str, target_id: str, edge_id: str = "edge00000001") -> GraphEdge:
    return GraphEdge(
        id=edge_id,
        source_id=source_id,
        target_id=target_id,
        edge_type=EdgeType.CONTROLS,
        rationale="GRJR sets the framework contract; Capelle cannot unilaterally cut rates.",
    )


# ---------------------------------------------------------------------------
# get — empty session
# ---------------------------------------------------------------------------


async def test_get_unknown_session_returns_none(store: BaseGraphStore) -> None:
    result = await store.get("no-such-session")
    assert result is None


# ---------------------------------------------------------------------------
# add_node → get round-trip
# ---------------------------------------------------------------------------


async def test_add_node_creates_graph(store: BaseGraphStore) -> None:
    node = _make_finding_node()
    returned = await store.add_node("sess001", node)
    assert returned.id == node.id

    graph = await store.get("sess001")
    assert graph is not None
    assert len(graph.nodes) == 1
    assert graph.nodes[0].id == node.id


async def test_add_node_bumps_version(store: BaseGraphStore) -> None:
    node = _make_finding_node()
    await store.add_node("sess001", node)
    graph = await store.get("sess001")
    assert graph is not None
    assert graph.version == 1


async def test_add_two_nodes_increments_version(store: BaseGraphStore) -> None:
    await store.add_node("sess001", _make_finding_node("id000000001"))
    await store.add_node("sess001", _make_context_node("id000000002"))
    graph = await store.get("sess001")
    assert graph is not None
    assert graph.version == 2
    assert len(graph.nodes) == 2


# ---------------------------------------------------------------------------
# add_edge → get round-trip
# ---------------------------------------------------------------------------


async def test_add_edge_bumps_version(store: BaseGraphStore) -> None:
    n1 = _make_finding_node("node00000001")
    n2 = _make_context_node("node00000002")
    await store.add_node("sess001", n1)
    await store.add_node("sess001", n2)
    edge = _make_edge("node00000001", "node00000002")
    returned_edge = await store.add_edge("sess001", edge)
    assert returned_edge.id == edge.id

    graph = await store.get("sess001")
    assert graph is not None
    assert len(graph.edges) == 1
    assert graph.version == 3


# ---------------------------------------------------------------------------
# set_summary
# ---------------------------------------------------------------------------


async def test_set_summary(store: BaseGraphStore) -> None:
    await store.add_node("sess001", _make_finding_node())
    await store.set_summary("sess001", "One strong finding confirmed.")
    graph = await store.get("sess001")
    assert graph is not None
    assert graph.summary == "One strong finding confirmed."


async def test_set_summary_bumps_version(store: BaseGraphStore) -> None:
    await store.add_node("sess001", _make_finding_node())
    v_before = (await store.get("sess001")).version  # type: ignore[union-attr]
    await store.set_summary("sess001", "Summary text.")
    graph = await store.get("sess001")
    assert graph is not None
    assert graph.version == v_before + 1


async def test_set_summary_replaces_previous(store: BaseGraphStore) -> None:
    await store.add_node("sess001", _make_finding_node())
    await store.set_summary("sess001", "First summary.")
    await store.set_summary("sess001", "Updated summary.")
    graph = await store.get("sess001")
    assert graph is not None
    assert graph.summary == "Updated summary."


# ---------------------------------------------------------------------------
# prune_node
# ---------------------------------------------------------------------------


async def test_prune_node_sets_status(store: BaseGraphStore) -> None:
    node = _make_finding_node("pruneme00001")
    await store.add_node("sess001", node)
    result = await store.prune_node("sess001", "pruneme00001")
    assert result is True
    graph = await store.get("sess001")
    assert graph is not None
    pruned = next(n for n in graph.nodes if n.id == "pruneme00001")
    assert pruned.status == NodeStatus.PRUNED


async def test_prune_node_bumps_version(store: BaseGraphStore) -> None:
    await store.add_node("sess001", _make_finding_node("pruneme00001"))
    v_before = (await store.get("sess001")).version  # type: ignore[union-attr]
    await store.prune_node("sess001", "pruneme00001")
    graph = await store.get("sess001")
    assert graph is not None
    assert graph.version == v_before + 1


async def test_prune_nonexistent_node_returns_false(store: BaseGraphStore) -> None:
    # Prune on a session with no graph is False.
    result = await store.prune_node("no-such-session", "no-such-node")
    assert result is False


async def test_prune_node_missing_from_session_returns_false(
    store: BaseGraphStore,
) -> None:
    await store.add_node("sess001", _make_finding_node("real000000001"))
    result = await store.prune_node("sess001", "ghost00000001")
    assert result is False


# ---------------------------------------------------------------------------
# Per-session node limit
# ---------------------------------------------------------------------------


async def test_node_limit_raises_when_exceeded(store: BaseGraphStore) -> None:
    """Adding more than MAX_NODES_PER_SESSION nodes must raise LimitExceededError."""
    from capelle_platform.graph.base_store import LimitExceededError

    # Add MAX_NODES_PER_SESSION valid context nodes (no evidence required).
    for i in range(MAX_NODES_PER_SESSION):
        nid = f"n{i:011d}"
        await store.add_node(
            "sess_limit",
            GraphNode(
                id=nid,
                node_type=NodeType.CONTEXT,
                claim=f"Context node number {i} of the session.",
                blocks=[],
                citations=[],
                confidence=Confidence.LOW,
                status=NodeStatus.PROPOSED,
                agent_label="lead",
                message_id="msg_limit",
            ),
        )
    with pytest.raises(LimitExceededError):
        await store.add_node(
            "sess_limit",
            GraphNode(
                id="overflow0001",
                node_type=NodeType.CONTEXT,
                claim="This node exceeds the per-session node limit.",
                blocks=[],
                citations=[],
                confidence=Confidence.LOW,
                status=NodeStatus.PROPOSED,
                agent_label="lead",
                message_id="msg_limit",
            ),
        )


# ---------------------------------------------------------------------------
# Per-session edge limit
# ---------------------------------------------------------------------------


async def test_edge_limit_raises_when_exceeded(store: BaseGraphStore) -> None:
    """Adding more than MAX_EDGES_PER_SESSION edges must raise LimitExceededError."""
    from capelle_platform.graph.base_store import LimitExceededError

    # We need at least 2 nodes to add edges.
    await store.add_node(
        "sess_elimit",
        GraphNode(
            id="src000000001",
            node_type=NodeType.CONTEXT,
            claim="Source context node.",
            blocks=[],
            citations=[],
            confidence=Confidence.LOW,
            status=NodeStatus.PROPOSED,
            agent_label="lead",
            message_id="msg_elimit",
        ),
    )
    await store.add_node(
        "sess_elimit",
        GraphNode(
            id="tgt000000001",
            node_type=NodeType.CONTEXT,
            claim="Target context node.",
            blocks=[],
            citations=[],
            confidence=Confidence.LOW,
            status=NodeStatus.PROPOSED,
            agent_label="lead",
            message_id="msg_elimit",
        ),
    )
    for i in range(MAX_EDGES_PER_SESSION):
        eid = f"e{i:011d}"
        await store.add_edge(
            "sess_elimit",
            GraphEdge(
                id=eid,
                source_id="src000000001",
                target_id="tgt000000001",
                edge_type=EdgeType.EXPLAINS,
                rationale=f"Edge rationale {i}.",
            ),
        )
    with pytest.raises(LimitExceededError):
        await store.add_edge(
            "sess_elimit",
            GraphEdge(
                id="overflow_edge",
                source_id="src000000001",
                target_id="tgt000000001",
                edge_type=EdgeType.EXPLAINS,
                rationale="This edge exceeds the per-session edge limit.",
            ),
        )


# ---------------------------------------------------------------------------
# Session isolation
# ---------------------------------------------------------------------------


async def test_sessions_are_isolated(store: BaseGraphStore) -> None:
    """Nodes added to sess_a must not appear in sess_b."""
    await store.add_node("sess_a", _make_finding_node("node_a_001"))
    graph_a = await store.get("sess_a")
    graph_b = await store.get("sess_b")
    assert graph_a is not None
    assert graph_b is None
    assert len(graph_a.nodes) == 1


# ---------------------------------------------------------------------------
# PostgresGraphStore — structural test (no live DB)
# ---------------------------------------------------------------------------


def test_postgres_store_has_create_table_sql() -> None:
    """
    PostgresGraphStore must reference CREATE TABLE IF NOT EXISTS analysis_graphs
    in the schema that it loads at initialization time.

    We verify this structurally by reading schema.sql directly —
    no live DB is required.
    """
    from pathlib import Path

    schema_path = (
        Path(__file__).resolve().parent.parent
        / "capelle_platform"
        / "store"
        / "schema.sql"
    )
    schema_text = schema_path.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS analysis_graphs" in schema_text
    assert "session_id" in schema_text
    assert "JSONB" in schema_text or "jsonb" in schema_text
    assert "version" in schema_text


def test_postgres_graph_store_is_importable() -> None:
    """PostgresGraphStore must be importable (structural check)."""
    from capelle_platform.graph.impl_postgres import PostgresGraphStore  # noqa: F401


@pytest.mark.asyncio
async def test_prune_missing_node_does_not_bump_version(store: BaseGraphStore) -> None:
    """A no-op prune (unknown node id) must NOT bump the graph version."""
    await store.add_node("sess001", _make_finding_node("real00000001"))
    before = await store.get("sess001")
    assert before is not None
    pruned = await store.prune_node("sess001", "ghost0000001")
    assert pruned is False
    after = await store.get("sess001")
    assert after is not None
    assert after.version == before.version
