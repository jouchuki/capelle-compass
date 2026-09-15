"""
Tests for Task 5 — node-scoped follow-ups.

Covers:
  - ChatMessageCreate accepts node_id (optional, default None)
  - JobMessage threads node_id through from the handler layer
  - OhrsExecutor._build_prompt injects a FOCUS NODE block when node_id is set
    and a graph + node exist
  - _build_prompt without node_id produces output byte-identical to today
    (regression guard)
  - Unknown node_id → identical output to no-node_id (regression guard)
  - Flag off (graph_enabled=False) → identical output (regression guard)
  - Neighbor cap is respected (constant, not magic number)

All tests are synchronous; _build_prompt is a staticmethod (after T5 it will
accept graph_store + settings, tested here via direct calls).

Test idioms:
  - settings_factory fixture (from conftest.py)
  - InMemoryGraphStore for graph access
  - Direct call to OhrsExecutor._build_prompt
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from capelle_platform.graph.impl_memory import InMemoryGraphStore
from capelle_platform.graph.models import (
    Confidence,
    EdgeType,
    GraphEdge,
    GraphNode,
    NodeType,
)
from capelle_platform.models.job import JobMessage
from capelle_platform.models.message import ChatMessageCreate
from capelle_platform.executor.impl_ohrs import OhrsExecutor
from capelle_platform.executor.prompt_focus import (
    MAX_FOCUS_NEIGHBORS,
    build_focus_node_block,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OUTPUT_PATH = "/tmp/jobs/test/analysis.json"
_SESSION_ID = "session-abc"
_NODE_ID = "aabbccddeeff"


def _make_node(
    node_id: str = _NODE_ID,
    claim: str = "Capelle spends 15% more per child than peer municipalities.",
    node_type: NodeType = NodeType.FINDING,
    confidence: Confidence = Confidence.MEDIUM,
    blocks: list[dict[str, Any]] | None = None,
    citations: list[dict[str, Any]] | None = None,
) -> GraphNode:
    """Build a minimal valid GraphNode for tests."""
    if blocks is None and citations is None:
        # finding/verification require evidence; supply a minimal citation
        citations = [{"source": "CBS Statline", "document": "Jeugdzorg 2023"}]
    return GraphNode(
        id=node_id,
        node_type=node_type,
        claim=claim,
        confidence=confidence,
        blocks=blocks or [],
        citations=citations or [],
    )


def _make_edge(
    edge_id: str,
    source: str,
    target: str,
    edge_type: EdgeType = EdgeType.EXPLAINS,
    rationale: str = "test rationale",
) -> GraphEdge:
    return GraphEdge(
        id=edge_id,
        source_id=source,
        target_id=target,
        edge_type=edge_type,
        rationale=rationale,
    )


def _base_job(node_id: str | None = None, history: list | None = None) -> JobMessage:
    return JobMessage(
        session_id=_SESSION_ID,
        message_id="msg-001",
        user_id="user-001",
        query="Why is jeugdzorg spending rising?",
        skill="capelle-analyse",
        trace_id="trace-001",
        history=history or [],
        node_id=node_id,
    )


def _build(
    job: JobMessage,
    graph_store: InMemoryGraphStore | None = None,
    graph_enabled: bool = True,
    settings_factory=None,
) -> str:
    """
    Thin wrapper that calls OhrsExecutor._build_prompt with a test Settings.
    """
    if settings_factory is not None:
        settings = settings_factory(graph_enabled=graph_enabled)
    else:
        from capelle_platform.settings import Settings
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        settings = Settings(
            sqlite_path=Path(tmp.name),
            jwt_secret="test-secret-minimum-32-characters-long-ok",
            graph_enabled=graph_enabled,
        )
    return asyncio.get_event_loop().run_until_complete(
        OhrsExecutor._build_prompt_async(job, _OUTPUT_PATH, graph_store, settings)
    )


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


def test_chat_message_create_accepts_node_id() -> None:
    """ChatMessageCreate must accept an optional node_id."""
    msg = ChatMessageCreate(content="Hello", node_id="aabbccddeeff")
    assert msg.node_id == "aabbccddeeff"


def test_chat_message_create_node_id_defaults_none() -> None:
    """Omitting node_id yields None — backward compatible."""
    msg = ChatMessageCreate(content="Hello")
    assert msg.node_id is None


def test_job_message_threads_node_id() -> None:
    """JobMessage must carry node_id with a default of None."""
    job = JobMessage(
        session_id="s",
        message_id="m",
        user_id="u",
        query="q",
        skill="sk",
        trace_id="tr",
    )
    assert job.node_id is None


def test_job_message_accepts_explicit_node_id() -> None:
    """JobMessage with an explicit node_id stores it correctly."""
    job = _base_job(node_id="aabbccddeeff")
    assert job.node_id == "aabbccddeeff"


# ---------------------------------------------------------------------------
# build_focus_node_block unit tests
# ---------------------------------------------------------------------------


def test_focus_block_contains_claim() -> None:
    """The FOCUS NODE block must include the node's claim."""
    node = _make_node()
    neighbor_lines: list[str] = []
    block = build_focus_node_block(node, neighbor_lines)
    assert node.claim in block


def test_focus_block_contains_node_type() -> None:
    """The FOCUS NODE block must include the node type."""
    node = _make_node()
    block = build_focus_node_block(node, [])
    assert node.node_type.value in block


def test_focus_block_contains_confidence() -> None:
    """The FOCUS NODE block must include the confidence level."""
    node = _make_node()
    block = build_focus_node_block(node, [])
    assert node.confidence.value in block


def test_focus_block_contains_evidence_count() -> None:
    """The block must show evidence COUNTS, not full blocks."""
    blocks = [{"type": "paragraph", "content": "some text"}, {"type": "paragraph", "content": "more"}]
    node = _make_node(blocks=blocks, citations=None)
    block = build_focus_node_block(node, [])
    # Should mention number of evidence blocks
    assert "2" in block


def test_focus_block_contains_citations() -> None:
    """Citations (source + document) must appear in the block."""
    node = _make_node(citations=[{"source": "CBS Statline", "document": "Jeugdzorg 2023"}])
    block = build_focus_node_block(node, [])
    assert "CBS Statline" in block
    assert "Jeugdzorg 2023" in block


def test_focus_block_contains_connect_instruction_with_node_id() -> None:
    """The connect instruction must reference the exact node id."""
    node = _make_node(node_id="deadbeef1234")
    block = build_focus_node_block(node, [])
    assert "deadbeef1234" in block
    assert "CONNECT" in block.upper() or "connect" in block


def test_focus_block_contains_edge_lines() -> None:
    """Neighbor lines are included verbatim in the block."""
    node = _make_node()
    neighbor_lines = ["-(explains)-> Some other finding"]
    block = build_focus_node_block(node, neighbor_lines)
    assert "-(explains)-> Some other finding" in block


def test_focus_block_empty_neighbors_still_valid() -> None:
    """An isolated node (no neighbors) produces a valid block without errors."""
    node = _make_node()
    block = build_focus_node_block(node, [])
    assert isinstance(block, str)
    assert len(block) > 0


# ---------------------------------------------------------------------------
# _build_prompt integration tests (via _build_prompt_async wrapper)
# ---------------------------------------------------------------------------


def test_build_prompt_with_graph_and_node_id_contains_claim(settings_factory) -> None:
    """When node_id is set and graph has the node, prompt contains the claim."""
    store = InMemoryGraphStore()
    node = _make_node()
    asyncio.get_event_loop().run_until_complete(
        store.add_node(_SESSION_ID, node)
    )
    job = _base_job(node_id=_NODE_ID)
    prompt = _build(job, graph_store=store, settings_factory=settings_factory)
    assert node.claim in prompt


def test_build_prompt_with_graph_and_node_id_contains_edge_lines(settings_factory) -> None:
    """When the node has neighbors, edge lines appear in the prompt."""
    store = InMemoryGraphStore()
    node_a = _make_node(node_id="aabbccddeeff", claim="Claim A")
    node_b = _make_node(
        node_id="112233445566",
        claim="Claim B — regional procurement controls costs",
        node_type=NodeType.CONTEXT,
    )
    edge = _make_edge("edge001", source="aabbccddeeff", target="112233445566", edge_type=EdgeType.EXPLAINS)
    asyncio.get_event_loop().run_until_complete(store.add_node(_SESSION_ID, node_a))
    asyncio.get_event_loop().run_until_complete(store.add_node(_SESSION_ID, node_b))
    asyncio.get_event_loop().run_until_complete(store.add_edge(_SESSION_ID, edge))
    job = _base_job(node_id="aabbccddeeff")
    prompt = _build(job, graph_store=store, settings_factory=settings_factory)
    assert "explains" in prompt
    assert "Claim B" in prompt


def test_build_prompt_with_graph_and_node_id_contains_connect_instruction(settings_factory) -> None:
    """The prompt must instruct the agent to CONNECT new findings to the node id."""
    store = InMemoryGraphStore()
    node = _make_node()
    asyncio.get_event_loop().run_until_complete(store.add_node(_SESSION_ID, node))
    job = _base_job(node_id=_NODE_ID)
    prompt = _build(job, graph_store=store, settings_factory=settings_factory)
    # The node ID must appear in the connect instruction
    assert _NODE_ID in prompt


def test_build_prompt_without_node_id_identical_to_baseline(settings_factory) -> None:
    """Without node_id the prompt is byte-identical to the no-graph baseline."""
    store = InMemoryGraphStore()
    node = _make_node()
    asyncio.get_event_loop().run_until_complete(store.add_node(_SESSION_ID, node))

    job_with = _base_job(node_id=None)
    job_without = _base_job(node_id=None)

    prompt_with_store = _build(job_with, graph_store=store, settings_factory=settings_factory)
    prompt_no_store = _build(job_without, graph_store=None, settings_factory=settings_factory)
    assert prompt_with_store == prompt_no_store


def test_build_prompt_unknown_node_id_identical_to_baseline(settings_factory) -> None:
    """An unknown node_id must yield a prompt identical to no-node_id."""
    store = InMemoryGraphStore()
    node = _make_node()
    asyncio.get_event_loop().run_until_complete(store.add_node(_SESSION_ID, node))

    job_unknown = _base_job(node_id="000000000000")  # not in graph
    job_baseline = _base_job(node_id=None)

    prompt_unknown = _build(job_unknown, graph_store=store, settings_factory=settings_factory)
    prompt_baseline = _build(job_baseline, graph_store=None, settings_factory=settings_factory)
    assert prompt_unknown == prompt_baseline


def test_build_prompt_flag_off_identical_to_baseline(settings_factory) -> None:
    """When graph_enabled=False, the prompt is identical to the no-node_id baseline."""
    store = InMemoryGraphStore()
    node = _make_node()
    asyncio.get_event_loop().run_until_complete(store.add_node(_SESSION_ID, node))

    job_flagged = _base_job(node_id=_NODE_ID)
    job_baseline = _base_job(node_id=None)

    prompt_flagged = _build(
        job_flagged, graph_store=store, graph_enabled=False, settings_factory=settings_factory
    )
    prompt_baseline = _build(
        job_baseline, graph_store=None, graph_enabled=True, settings_factory=settings_factory
    )
    assert prompt_flagged == prompt_baseline


def test_build_prompt_no_graph_store_identical_to_baseline(settings_factory) -> None:
    """When graph_store is None (no graph injected), prompt is identical to baseline."""
    job_with_node_id = _base_job(node_id=_NODE_ID)
    job_baseline = _base_job(node_id=None)

    prompt_with_node_id = _build(job_with_node_id, graph_store=None, settings_factory=settings_factory)
    prompt_baseline = _build(job_baseline, graph_store=None, settings_factory=settings_factory)
    assert prompt_with_node_id == prompt_baseline


# ---------------------------------------------------------------------------
# Neighbor cap test
# ---------------------------------------------------------------------------


def test_neighbor_cap_respected(settings_factory) -> None:
    """The number of neighbor lines is capped at MAX_FOCUS_NEIGHBORS."""
    store = InMemoryGraphStore()
    # Create one focus node
    focus = _make_node(node_id="focusnode0001", claim="Focus finding")
    asyncio.get_event_loop().run_until_complete(store.add_node(_SESSION_ID, focus))

    # Create MAX_FOCUS_NEIGHBORS + 3 neighbor nodes and edges
    over = MAX_FOCUS_NEIGHBORS + 3
    neighbor_ids = []
    for i in range(over):
        nid = f"neighbor{i:06d}"
        neighbor_ids.append(nid)
        n = GraphNode(
            id=nid,
            node_type=NodeType.CONTEXT,
            claim=f"Context node number {i} in the neighbor set",
            confidence=Confidence.LOW,
        )
        asyncio.get_event_loop().run_until_complete(store.add_node(_SESSION_ID, n))
        e = _make_edge(
            f"edge{i:06d}",
            source="focusnode0001",
            target=nid,
            edge_type=EdgeType.CONTROLS,
        )
        asyncio.get_event_loop().run_until_complete(store.add_edge(_SESSION_ID, e))

    job = _base_job(node_id="focusnode0001")
    prompt = _build(job, graph_store=store, settings_factory=settings_factory)

    # Count how many neighbor lines appear: each starts with "-(
    edge_lines_in_prompt = [
        line for line in prompt.splitlines()
        if line.strip().startswith("-(")
    ]
    assert len(edge_lines_in_prompt) <= MAX_FOCUS_NEIGHBORS


def test_build_prompt_pruned_node_identical_to_baseline(settings_factory) -> None:
    """A PRUNED focus node is dead — must yield the no-node_id baseline prompt."""
    from capelle_platform.graph.models import NodeStatus

    store = InMemoryGraphStore()
    node = _make_node()
    pruned = node.model_copy(update={"status": NodeStatus.PRUNED})
    asyncio.get_event_loop().run_until_complete(store.add_node(_SESSION_ID, pruned))

    job_pruned = _base_job(node_id=_NODE_ID)
    job_baseline = _base_job(node_id=None)

    prompt_pruned = _build(job_pruned, graph_store=store, settings_factory=settings_factory)
    prompt_baseline = _build(job_baseline, graph_store=None, settings_factory=settings_factory)
    assert prompt_pruned == prompt_baseline


def test_focus_prompt_never_starts_with_hyphen(settings_factory) -> None:
    """The prompt rides into ohrs as `-p <prompt>` argv: a leading '-' would be
    parsed as a flag by clap and kill the job with exit 2. Regression for the
    '--- FOCUS NODE ---' argv-injection crash (2026-06-10)."""
    store = InMemoryGraphStore()
    node = _make_node()
    asyncio.get_event_loop().run_until_complete(store.add_node(_SESSION_ID, node))

    job = _base_job(node_id=_NODE_ID)
    prompt = _build(job, graph_store=store, settings_factory=settings_factory)
    assert "FOCUS NODE" in prompt  # injection actually happened
    assert not prompt.startswith("-"), "prompt must never begin with a hyphen"


def test_multi_node_scope_injects_block_per_node(settings_factory) -> None:
    """node_ids=[a,b] injects one FOCUS NODE block per node, in order."""
    store = InMemoryGraphStore()
    a = _make_node(node_id="aaaaaaaaaaa1", claim="Eerste bevinding over kosten.")
    b = _make_node(node_id="bbbbbbbbbbb2", claim="Tweede bevinding over volume.")
    loop = asyncio.get_event_loop()
    loop.run_until_complete(store.add_node(_SESSION_ID, a))
    loop.run_until_complete(store.add_node(_SESSION_ID, b))

    job = _base_job(node_id=None)
    job = job.model_copy(update={"node_ids": ["aaaaaaaaaaa1", "bbbbbbbbbbb2"]})
    prompt = _build(job, graph_store=store, settings_factory=settings_factory)
    assert prompt.count("=== FOCUS NODE ===") == 2
    assert prompt.index("Eerste bevinding") < prompt.index("Tweede bevinding")


def test_multi_node_scope_skips_unknown_and_pruned(settings_factory) -> None:
    """Unknown and pruned ids are skipped; remaining valid ids still inject."""
    from capelle_platform.graph.models import NodeStatus

    store = InMemoryGraphStore()
    a = _make_node(node_id="aaaaaaaaaaa1")
    pruned = _make_node(node_id="ccccccccccc3").model_copy(
        update={"status": NodeStatus.PRUNED}
    )
    loop = asyncio.get_event_loop()
    loop.run_until_complete(store.add_node(_SESSION_ID, a))
    loop.run_until_complete(store.add_node(_SESSION_ID, pruned))

    job = _base_job(node_id=None).model_copy(
        update={"node_ids": ["ghost0000001", "ccccccccccc3", "aaaaaaaaaaa1"]}
    )
    prompt = _build(job, graph_store=store, settings_factory=settings_factory)
    assert prompt.count("=== FOCUS NODE ===") == 1


def test_multi_node_scope_caps_at_max(settings_factory) -> None:
    """More than MAX_FOCUS_NODES scoped ids are capped (first N win)."""
    from capelle_platform.executor.prompt_focus import MAX_FOCUS_NODES

    store = InMemoryGraphStore()
    loop = asyncio.get_event_loop()
    ids = []
    for i in range(MAX_FOCUS_NODES + 3):
        nid = f"node{i:08d}"
        loop.run_until_complete(
            store.add_node(_SESSION_ID, _make_node(node_id=nid, claim=f"Bevinding {i}."))
        )
        ids.append(nid)

    job = _base_job(node_id=None).model_copy(update={"node_ids": ids})
    prompt = _build(job, graph_store=store, settings_factory=settings_factory)
    assert prompt.count("=== FOCUS NODE ===") == MAX_FOCUS_NODES
