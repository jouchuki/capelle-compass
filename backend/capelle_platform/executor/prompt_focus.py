"""
Focus-node context injection for node-scoped follow-up prompts.

When a :class:`~capelle_platform.models.job.JobMessage` carries a
``node_id``, :func:`build_focus_node_block` produces a compact "FOCUS NODE"
block that is prepended to the executor prompt by
:meth:`OhrsExecutor._build_prompt_async`.  The block gives the agent the
node's claim, type, confidence, evidence count (not full blocks — compact),
citations (source + document only), and its 1-hop neighborhood as edge lines.

Named constants
---------------
``MAX_FOCUS_NEIGHBORS`` caps the number of neighbor lines so the block stays
predictably small regardless of how dense the graph becomes.

Regression contract
-------------------
When ``node_id`` is absent, the flag is off, the graph store is not injected,
or the node cannot be resolved, this module is not invoked and the prompt is
byte-identical to the pre-Task-5 baseline.  The caller
(:func:`OhrsExecutor._build_prompt_async`) enforces these guards; this module
only handles the *positive* path.
"""

from __future__ import annotations

from typing import Final

from capelle_platform.graph.models import GraphEdge, GraphNode

# Maximum number of 1-hop neighbor lines included in the FOCUS NODE block.
# Keeping this constant small ensures the block is always compact regardless
# of how well-connected the focus node is.
MAX_FOCUS_NEIGHBORS: Final[int] = 10

# Maximum number of FOCUS NODE blocks per follow-up: a user may scope a
# question to several findings at once; beyond this the context stops being
# "focused" and the blocks crowd out the actual question.
MAX_FOCUS_NODES: Final[int] = 5


def _collect_neighbor_lines(
    focus_id: str,
    edges: list[GraphEdge],
    nodes_by_id: dict[str, GraphNode],
) -> list[str]:
    """
    Build compact neighbor lines for the 1-hop neighbourhood of ``focus_id``.

    Each outbound edge becomes ``-(edge_type)-> <claim>``; each inbound edge
    becomes ``<-(edge_type)- <claim>``.  The list is capped at
    ``MAX_FOCUS_NEIGHBORS`` entries (total, both directions) to keep the block
    compact regardless of graph density.

    Args:
        focus_id:    The node whose neighbourhood is being described.
        edges:       All edges in the session graph.
        nodes_by_id: Mapping of node_id → GraphNode for the session.

    Returns:
        A list of formatted edge lines, at most ``MAX_FOCUS_NEIGHBORS`` long.
    """
    lines: list[str] = []
    for edge in edges:
        if len(lines) >= MAX_FOCUS_NEIGHBORS:
            break
        if edge.source_id == focus_id:
            neighbour = nodes_by_id.get(edge.target_id)
            if neighbour is not None:
                lines.append(f"-({edge.edge_type.value})-> {neighbour.claim}")
        elif edge.target_id == focus_id:
            neighbour = nodes_by_id.get(edge.source_id)
            if neighbour is not None:
                lines.append(f"<-({edge.edge_type.value})- {neighbour.claim}")
    return lines[:MAX_FOCUS_NEIGHBORS]


def build_focus_node_block(
    node: GraphNode,
    neighbor_lines: list[str],
) -> str:
    """
    Render the compact FOCUS NODE block for prompt injection.

    The block is intentionally terse: claim, type, confidence, evidence block
    COUNT (not the full block contents — those can be multi-kilobyte), citation
    source+document pairs, neighbour edge lines, and an instruction to deepen/
    verify/branch from this node and CONNECT new findings to it.

    Args:
        node:           The graph node the user is asking about.
        neighbor_lines: Pre-computed 1-hop edge lines (see
                        :func:`_collect_neighbor_lines`).

    Returns:
        A multi-line string ready to be prepended to the user-facing prompt.
    """
    block_count = len(node.blocks)
    citation_lines: list[str] = []
    for cite in node.citations:
        source = cite.get("source", "")
        document = cite.get("document", "")
        if source or document:
            citation_lines.append(f"  - {source}: {document}".strip(" -").strip())

    neighbour_section = ""
    if neighbor_lines:
        neighbour_section = "\n1-hop neighbourhood:\n" + "\n".join(
            f"  {line}" for line in neighbor_lines
        )

    citation_section = ""
    if citation_lines:
        citation_section = "\nCitations:\n" + "\n".join(
            f"  - {c}" for c in citation_lines
        )

    connect_instruction = (
        f"The user is asking specifically about this finding. "
        f"Deepen, verify, or branch from it; record new findings via "
        f"capelle-graph and CONNECT them to node {node.id}."
    )

    # NOTE: the prompt reaches the ohrs binary as `-p <prompt>` argv. It must
    # NEVER begin with a hyphen — clap parses a leading '-' as a flag and the
    # job dies with exit 2 ("unexpected argument '--- FOCUS NODE ---…'").
    # Hence '===' fences instead of '---'.
    return (
        "=== FOCUS NODE ===\n"
        f"id: {node.id}\n"
        f"type: {node.node_type.value}\n"
        f"confidence: {node.confidence.value}\n"
        f"claim: {node.claim}\n"
        f"evidence blocks: {block_count}"
        f"{citation_section}"
        f"{neighbour_section}\n"
        f"{connect_instruction}\n"
        "=== END FOCUS NODE ===\n"
    )
