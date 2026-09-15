"""
Pydantic v2 domain models for the finding-graph.

Types
-----
NodeType     : StrEnum — finding | context | hypothesis | verification
EdgeType     : StrEnum — causes | explains | controls | tensions_with |
                         depends_on | decomposes_into
Confidence   : StrEnum — low | medium | high
NodeStatus   : StrEnum — proposed | supported | verified | pruned

GraphNode    : a single node in the investigation graph
GraphEdge    : a directed, typed relationship between two nodes
FindingGraph : the session-scoped graph (nodes, edges, summary, version)

Validity rules (enforced at the model layer, not just the endpoint layer):
  - A ``finding`` or ``verification`` node with empty ``blocks`` AND empty
    ``citations`` is rejected: null results and vibes are not nodes.
  - Edge ``source_id`` and ``target_id`` must differ (no self-loops).
  - ``claim`` must be non-empty and at most ``MAX_CLAIM_LEN`` characters.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, model_validator

from capelle_platform.graph.constants import MAX_CLAIM_LEN


class NodeType(str, enum.Enum):
    """Semantic role of a graph node."""

    FINDING = "finding"
    CONTEXT = "context"
    HYPOTHESIS = "hypothesis"
    VERIFICATION = "verification"


class EdgeType(str, enum.Enum):
    """Typed relationship between two graph nodes."""

    CAUSES = "causes"
    EXPLAINS = "explains"
    CONTROLS = "controls"
    TENSIONS_WITH = "tensions_with"
    DEPENDS_ON = "depends_on"
    DECOMPOSES_INTO = "decomposes_into"


class Confidence(str, enum.Enum):
    """Analyst confidence in a node claim."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class NodeStatus(str, enum.Enum):
    """Lifecycle state of a graph node."""

    PROPOSED = "proposed"
    SUPPORTED = "supported"
    VERIFIED = "verified"
    PRUNED = "pruned"


# Evidence types are stored as dicts to reuse the existing v2 Block and
# CitationRef schemas without a circular dependency on the report package.
# The graph layer is agnostic to their internal structure; validation of
# individual block shapes lives in the existing block-rendering pipeline.
_BlockDict = dict[str, Any]
_CitationRefDict = dict[str, Any]


class GraphNode(BaseModel):
    """
    A single node in the finding-graph.

    Provenance fields (agent_label, message_id, created_at, trace_id) are
    set by the server or the calling layer; the store never mutates them.
    """

    model_config = {"frozen": True}

    id: str = Field(description="Server-assigned 12-hex node identifier.")
    node_type: NodeType
    claim: str = Field(
        min_length=1,
        max_length=MAX_CLAIM_LEN,
        description="One-sentence claim. Must not exceed MAX_CLAIM_LEN characters.",
    )

    # Evidence — reuses v2 Block dicts and CitationRef dicts.
    blocks: list[_BlockDict] = Field(default_factory=list)
    citations: list[_CitationRefDict] = Field(default_factory=list)

    confidence: Confidence
    status: NodeStatus = NodeStatus.PROPOSED

    # Provenance
    agent_label: str = Field(default="")
    message_id: str = Field(default="")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    trace_id: str | None = Field(default=None)

    @model_validator(mode="after")
    def _require_evidence_for_finding_or_verification(self) -> "GraphNode":
        """
        Reject a ``finding`` or ``verification`` node that carries no evidence.

        This implements the "null results and vibes are not nodes" rule from
        the spec. ``context`` and ``hypothesis`` nodes are exempt — they may
        be constructed from prior knowledge without direct citations.
        """
        if self.node_type in (NodeType.FINDING, NodeType.VERIFICATION):
            if not self.blocks and not self.citations:
                raise ValueError(
                    f"A '{self.node_type.value}' node requires at least one "
                    "evidence block or citation. "
                    "Nodes without evidence are not permitted."
                )
        return self


class GraphEdge(BaseModel):
    """
    A directed, typed relationship between two graph nodes.

    The ``rationale`` field carries a one-sentence justification; for
    ``causes`` edges the spec asks that the rationale name the counterfactual
    — that discipline is enforced at the prompt layer, not here.
    """

    model_config = {"frozen": True}

    id: str = Field(description="Server-assigned 12-hex edge identifier.")
    source_id: str
    target_id: str
    edge_type: EdgeType
    rationale: str = Field(
        default="",
        description=(
            "Substantive explanation of the relationship: the mechanism, the "
            "evidence behind it, and (for causes) the named counterfactual. "
            "2-4 sentences — an edge is an analytical claim, not a label."
        ),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @model_validator(mode="after")
    def _reject_self_loops(self) -> "GraphEdge":
        if self.source_id == self.target_id:
            raise ValueError(
                f"source_id and target_id must differ; got '{self.source_id}' "
                "for both endpoints. Self-loops are not permitted."
            )
        return self


class FindingGraph(BaseModel):
    """
    The session-scoped finding-graph.

    Stored as JSONB in ``analysis_graphs``; the ``version`` field is used
    for optimistic concurrency control (incremented on every mutation).
    """

    session_id: str
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    summary: str | None = None
    version: int = 0

    def copy_with(self, **updates: Any) -> "FindingGraph":
        """Return a new FindingGraph with the given fields replaced."""
        return self.model_copy(update=updates)
