"""
In-memory finding-graph store.

Backed by a plain dict keyed by session_id. All mutations are
synchronous on the asyncio event loop — no locking is needed in the
single-threaded CPython event loop model.

Suitable for:
  - test suites (no database setup required)
  - single-process local dev

Not suitable for:
  - multi-process or multi-host deployments (use PostgresGraphStore)
"""

from __future__ import annotations

from capelle_platform.graph.base_store import BaseGraphStore, LimitExceededError
from capelle_platform.graph.models import (
    FindingGraph,
    GraphEdge,
    GraphNode,
    NodeStatus,
)
from capelle_platform.observability import get_logger

_logger = get_logger(__name__)


class InMemoryGraphStore(BaseGraphStore):
    """
    ``BaseGraphStore`` backed by ``dict[str, FindingGraph]``.

    Every mutation rebuilds the ``FindingGraph`` immutably (pydantic frozen
    models) by constructing a new instance with the updated field values and
    an incremented ``version``. This is intentionally simple — the in-memory
    store is for tests and local dev, not high-throughput production.
    """

    def __init__(self) -> None:
        """Initialise with an empty session map."""
        self._graphs: dict[str, FindingGraph] = {}

    # ------------------------------------------------------------------
    # BaseGraphStore implementation
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """No-op: the in-memory store needs no setup."""

    async def close(self) -> None:
        """No-op: the in-memory store holds no external resources."""

    async def get(self, session_id: str) -> FindingGraph | None:
        """Return the graph for ``session_id``, or ``None`` if absent."""
        return self._graphs.get(session_id)

    async def add_node(self, session_id: str, node: GraphNode) -> GraphNode:
        """
        Append ``node`` to the session graph, creating it if needed.

        Enforces ``MAX_NODES_PER_SESSION`` via the shared
        ``_check_node_limit`` helper before any mutation.
        """
        graph = self._graphs.get(session_id)
        if graph is None:
            graph = FindingGraph(session_id=session_id)

        self._check_node_limit(graph)

        updated = graph.copy_with(
            nodes=[*graph.nodes, node],
            version=graph.version + 1,
        )
        self._graphs[session_id] = updated
        _logger.debug(
            "graph_node_added",
            session_id=session_id,
            node_id=node.id,
            node_type=node.node_type.value,
            version=updated.version,
        )
        return node

    async def add_edge(self, session_id: str, edge: GraphEdge) -> GraphEdge:
        """
        Append ``edge`` to the session graph.

        Enforces ``MAX_EDGES_PER_SESSION`` via the shared
        ``_check_edge_limit`` helper before any mutation. The graph is
        created if absent so callers that add edges before the first node
        still get a coherent graph (the service layer should prevent this
        in practice, but the store is not the gatekeeper for that rule).
        """
        graph = self._graphs.get(session_id)
        if graph is None:
            graph = FindingGraph(session_id=session_id)

        self._check_edge_limit(graph)

        updated = graph.copy_with(
            edges=[*graph.edges, edge],
            version=graph.version + 1,
        )
        self._graphs[session_id] = updated
        _logger.debug(
            "graph_edge_added",
            session_id=session_id,
            edge_id=edge.id,
            edge_type=edge.edge_type.value,
            version=updated.version,
        )
        return edge

    async def set_summary(self, session_id: str, text: str) -> None:
        """
        Replace the summary for ``session_id``, creating the graph if needed.
        """
        graph = self._graphs.get(session_id)
        if graph is None:
            graph = FindingGraph(session_id=session_id)

        updated = graph.copy_with(
            summary=text,
            version=graph.version + 1,
        )
        self._graphs[session_id] = updated
        _logger.debug(
            "graph_summary_set",
            session_id=session_id,
            version=updated.version,
        )

    async def prune_node(self, session_id: str, node_id: str) -> bool:
        """
        Mark ``node_id`` as ``NodeStatus.PRUNED``.

        Returns ``True`` on success, ``False`` if the session or node is
        not found.
        """
        graph = self._graphs.get(session_id)
        if graph is None:
            return False

        new_nodes: list[GraphNode] = []
        found = False
        for n in graph.nodes:
            if n.id == node_id:
                new_nodes.append(n.model_copy(update={"status": NodeStatus.PRUNED}))
                found = True
            else:
                new_nodes.append(n)

        if not found:
            return False

        updated = graph.copy_with(
            nodes=new_nodes,
            version=graph.version + 1,
        )
        self._graphs[session_id] = updated
        _logger.debug(
            "graph_node_pruned",
            session_id=session_id,
            node_id=node_id,
            version=updated.version,
        )
        return True
