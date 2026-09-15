"""
Model validation tests for the finding-graph domain layer.

TDD: these tests are written FIRST and must fail until the implementation
is in place. They cover:
  - NodeType / EdgeType / Confidence / NodeStatus enum values
  - GraphNode: claim length, no-evidence rejection for finding/verification
  - GraphEdge: non-equal endpoint constraint
  - FindingGraph: round-trip construction
"""

from __future__ import annotations

import pytest

# All imports from the graph package will fail until the package exists.
from capelle_platform.graph.constants import (
    MAX_CLAIM_LEN,
    MAX_NODES_PER_SESSION,
    MAX_EDGES_PER_SESSION,
    MAX_BLOCKS_PER_NODE,
    DEDUP_RATIO,
    NODE_ID_HEX_LEN,
)
from capelle_platform.graph.models import (
    NodeType,
    EdgeType,
    Confidence,
    NodeStatus,
    GraphNode,
    GraphEdge,
    FindingGraph,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_constants_values() -> None:
    """Named constants must match the spec values exactly."""
    assert MAX_CLAIM_LEN == 280
    assert MAX_NODES_PER_SESSION == 300
    assert MAX_EDGES_PER_SESSION == 1200
    assert MAX_BLOCKS_PER_NODE == 30
    assert DEDUP_RATIO == 0.90
    assert NODE_ID_HEX_LEN == 12


# ---------------------------------------------------------------------------
# Enum membership
# ---------------------------------------------------------------------------


def test_node_type_values() -> None:
    values = {e.value for e in NodeType}
    assert values == {"finding", "context", "hypothesis", "verification"}


def test_edge_type_values() -> None:
    values = {e.value for e in EdgeType}
    assert values == {
        "causes",
        "explains",
        "controls",
        "tensions_with",
        "depends_on",
        "decomposes_into",
    }


def test_confidence_values() -> None:
    values = {e.value for e in Confidence}
    assert values == {"low", "medium", "high"}


def test_node_status_values() -> None:
    values = {e.value for e in NodeStatus}
    assert values == {"proposed", "supported", "verified", "pruned"}


# ---------------------------------------------------------------------------
# GraphNode — valid construction
# ---------------------------------------------------------------------------


def _valid_finding_node(**overrides) -> GraphNode:
    """Return a valid finding node with evidence blocks."""
    defaults: dict = {
        "id": "abc123def456",
        "node_type": NodeType.FINDING,
        "claim": "Capelle spends 20% more per youth-care case than peer cities.",
        "blocks": [{"type": "text", "text": "Source: CBS 2025 dataset."}],
        "citations": [],
        "confidence": Confidence.MEDIUM,
        "status": NodeStatus.PROPOSED,
        "agent_label": "lead",
        "message_id": "msg001",
    }
    defaults.update(overrides)
    return GraphNode(**defaults)


def test_valid_finding_node_construction() -> None:
    node = _valid_finding_node()
    assert node.node_type == NodeType.FINDING
    assert node.confidence == Confidence.MEDIUM
    assert node.status == NodeStatus.PROPOSED


def test_context_node_no_evidence_required() -> None:
    """context and hypothesis nodes do NOT require evidence."""
    node = GraphNode(
        id="abc123def456",
        node_type=NodeType.CONTEXT,
        claim="GRJR controls regional youth-care procurement for Capelle.",
        blocks=[],
        citations=[],
        confidence=Confidence.HIGH,
        status=NodeStatus.SUPPORTED,
        agent_label="lead",
        message_id="msg001",
    )
    assert node.node_type == NodeType.CONTEXT


def test_hypothesis_node_no_evidence_required() -> None:
    node = GraphNode(
        id="abc123def456",
        node_type=NodeType.HYPOTHESIS,
        claim="Rising PGB volume explains the cost growth.",
        blocks=[],
        citations=[],
        confidence=Confidence.LOW,
        status=NodeStatus.PROPOSED,
        agent_label="lead",
        message_id="msg001",
    )
    assert node.node_type == NodeType.HYPOTHESIS


# ---------------------------------------------------------------------------
# GraphNode — no-evidence rejection rule
# ---------------------------------------------------------------------------


def test_finding_node_rejects_empty_evidence() -> None:
    """A finding with no blocks AND no citations must raise ValidationError."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="finding"):
        GraphNode(
            id="abc123def456",
            node_type=NodeType.FINDING,
            claim="Some claim.",
            blocks=[],
            citations=[],
            confidence=Confidence.LOW,
            status=NodeStatus.PROPOSED,
            agent_label="lead",
            message_id="msg001",
        )


def test_verification_node_rejects_empty_evidence() -> None:
    """A verification with no blocks AND no citations must raise ValidationError."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="verification"):
        GraphNode(
            id="abc123def456",
            node_type=NodeType.VERIFICATION,
            claim="Verified: the cost gap holds across 2022-2024.",
            blocks=[],
            citations=[],
            confidence=Confidence.HIGH,
            status=NodeStatus.VERIFIED,
            agent_label="lead",
            message_id="msg001",
        )


def test_finding_with_only_citations_is_valid() -> None:
    """Citations alone satisfy the evidence requirement."""
    node = GraphNode(
        id="abc123def456",
        node_type=NodeType.FINDING,
        claim="Capelle has the highest youth-care spend per child in the panel.",
        blocks=[],
        citations=[{"source_url": "https://example.com/data", "text": "data"}],
        confidence=Confidence.HIGH,
        status=NodeStatus.SUPPORTED,
        agent_label="lead",
        message_id="msg001",
    )
    assert len(node.citations) == 1


# ---------------------------------------------------------------------------
# GraphNode — claim length
# ---------------------------------------------------------------------------


def test_claim_length_at_max_allowed() -> None:
    """A claim of exactly MAX_CLAIM_LEN characters must be accepted."""
    claim = "x" * MAX_CLAIM_LEN
    node = _valid_finding_node(claim=claim)
    assert len(node.claim) == MAX_CLAIM_LEN


def test_claim_too_long_is_rejected() -> None:
    """A claim exceeding MAX_CLAIM_LEN characters must raise ValidationError."""
    from pydantic import ValidationError

    long_claim = "x" * (MAX_CLAIM_LEN + 1)
    with pytest.raises(ValidationError):
        _valid_finding_node(claim=long_claim)


def test_empty_claim_is_rejected() -> None:
    """An empty claim must raise ValidationError."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _valid_finding_node(claim="")


# ---------------------------------------------------------------------------
# GraphEdge — valid construction
# ---------------------------------------------------------------------------


def _valid_edge(**overrides) -> GraphEdge:
    defaults: dict = {
        "id": "edgeid000001",
        "source_id": "src000000001",
        "target_id": "tgt000000001",
        "edge_type": EdgeType.CAUSES,
        "rationale": "Diff-in-diff 2019-2024 vs 6 donor cities; gap opens post-2021.",
    }
    defaults.update(overrides)
    return GraphEdge(**defaults)


def test_valid_edge_construction() -> None:
    edge = _valid_edge()
    assert edge.edge_type == EdgeType.CAUSES


def test_edge_self_loop_rejected() -> None:
    """source_id == target_id must raise ValidationError."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="source_id"):
        _valid_edge(source_id="same000000001", target_id="same000000001")


def test_edge_type_enum_all_values() -> None:
    """Ensure all six edge types can be used to construct an edge."""
    for et in EdgeType:
        edge = _valid_edge(edge_type=et)
        assert edge.edge_type == et


# ---------------------------------------------------------------------------
# FindingGraph
# ---------------------------------------------------------------------------


def test_empty_finding_graph() -> None:
    graph = FindingGraph(session_id="sess001")
    assert graph.nodes == []
    assert graph.edges == []
    assert graph.summary is None
    assert graph.version == 0


def test_finding_graph_with_content() -> None:
    node = _valid_finding_node()
    edge = _valid_edge()
    graph = FindingGraph(
        session_id="sess001",
        nodes=[node],
        edges=[edge],
        summary="One strong finding: Capelle pays more than peers.",
        version=3,
    )
    assert len(graph.nodes) == 1
    assert len(graph.edges) == 1
    assert graph.version == 3


def test_finding_graph_serialises_to_dict() -> None:
    """model_dump() must succeed (used for JSONB storage)."""
    node = _valid_finding_node()
    graph = FindingGraph(session_id="sess001", nodes=[node])
    dumped = graph.model_dump()
    assert dumped["session_id"] == "sess001"
    assert len(dumped["nodes"]) == 1


def test_finding_graph_serialises_to_json_mode() -> None:
    """model_dump(mode='json') must produce JSON-safe types (Postgres JSONB path)."""
    node = _valid_finding_node()
    graph = FindingGraph(session_id="sess001", nodes=[node])
    dumped = graph.model_dump(mode="json")
    assert isinstance(dumped["nodes"][0]["created_at"], str)  # datetime -> ISO str
    assert isinstance(dumped["nodes"][0]["node_type"], str)  # enum -> str value
    restored = FindingGraph.model_validate(dumped)
    assert restored.nodes[0].node_type == NodeType.FINDING


def test_copy_with_replaces_field_and_preserves_others() -> None:
    """copy_with applies the update, preserves the rest, returns a new instance."""
    graph = FindingGraph(session_id="sess001", version=2)
    updated = graph.copy_with(version=3)
    assert updated.version == 3
    assert updated.session_id == "sess001"
    assert updated is not graph
