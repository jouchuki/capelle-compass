"""
Abstract base class for the finding-graph persistence store.

Defines the async contract for graph CRUD operations. All per-session
limits are enforced HERE (or by implementations that call
``_check_node_limit`` / ``_check_edge_limit``) so the rule lives in
exactly one place and is tested at the base-class level.

Implementations
---------------
InMemoryGraphStore  — tests / single-process (impl_memory.py)
PostgresGraphStore  — production (impl_postgres.py)
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from capelle_platform.graph.constants import (
    MAX_EDGES_PER_SESSION,
    MAX_NODES_PER_SESSION,
)
from capelle_platform.graph.models import FindingGraph, GraphEdge, GraphNode


class LimitExceededError(Exception):
    """
    Raised when a mutation would push a per-session counter past its cap.

    Callers (endpoints) translate this to HTTP 422 with a descriptive body.
    The ``limit`` attribute carries the constant that was exceeded so tests
    can assert on the exact threshold without string-matching the message.
    """

    def __init__(self, resource: str, limit: int, current: int) -> None:
        self.resource = resource
        self.limit = limit
        self.current = current
        super().__init__(
            f"Per-session {resource} limit exceeded: "
            f"current={current}, limit={limit}."
        )


class BaseGraphStore(ABC):
    """
    Persistence contract for finding-graphs.

    All methods are async to support non-blocking IO regardless of the
    underlying driver. Implementations must bump ``FindingGraph.version``
    by exactly 1 on every successful mutation (add_node, add_edge,
    set_summary, prune_node that finds its target).
    """

    # ------------------------------------------------------------------
    # Internal limit helpers (shared logic, one definition)
    # ------------------------------------------------------------------

    @staticmethod
    def _check_node_limit(graph: FindingGraph) -> None:
        """
        Raise ``LimitExceededError`` if ``graph`` already has
        ``MAX_NODES_PER_SESSION`` nodes.

        Call this inside ``add_node`` BEFORE appending the new node.
        """
        current = len(graph.nodes)
        if current >= MAX_NODES_PER_SESSION:
            raise LimitExceededError(
                resource="nodes",
                limit=MAX_NODES_PER_SESSION,
                current=current,
            )

    @staticmethod
    def _check_edge_limit(graph: FindingGraph) -> None:
        """
        Raise ``LimitExceededError`` if ``graph`` already has
        ``MAX_EDGES_PER_SESSION`` edges.

        Call this inside ``add_edge`` BEFORE appending the new edge.
        """
        current = len(graph.edges)
        if current >= MAX_EDGES_PER_SESSION:
            raise LimitExceededError(
                resource="edges",
                limit=MAX_EDGES_PER_SESSION,
                current=current,
            )

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    @abstractmethod
    async def get(self, session_id: str) -> FindingGraph | None:
        """
        Fetch the graph for ``session_id``.

        Returns ``None`` when no graph exists yet for this session — the
        caller interprets the absence as "no graph yet", not an error.
        """

    @abstractmethod
    async def initialize(self) -> None:
        """
        Prepare the store for use (open pools, ensure schema). Mirrors the
        sibling ``BaseStore`` lifecycle so the AppBuilder can drive any
        implementation through the ABC without knowing the concrete type.
        In-memory implementations may no-op.
        """

    @abstractmethod
    async def close(self) -> None:
        """Release resources (drain pools). In-memory implementations may no-op."""

    @abstractmethod
    async def add_node(self, session_id: str, node: GraphNode) -> GraphNode:
        """
        Append ``node`` to the graph for ``session_id``, creating the
        graph entry if it does not yet exist.

        Raises ``LimitExceededError`` when the session already has
        ``MAX_NODES_PER_SESSION`` nodes.

        Bumps ``version`` by 1. Returns the stored node unchanged.
        """

    @abstractmethod
    async def add_edge(self, session_id: str, edge: GraphEdge) -> GraphEdge:
        """
        Append ``edge`` to the graph for ``session_id``.

        Raises ``LimitExceededError`` when the session already has
        ``MAX_EDGES_PER_SESSION`` edges. Creates the graph entry if absent —
        the store does NOT reject out-of-order calls (an edge before any
        node); enforcing that ordering (e.g. rejecting edges whose endpoint
        nodes do not exist) is the SERVICE layer's job, not the store's.

        Bumps ``version`` by 1. Returns the stored edge unchanged.
        """

    @abstractmethod
    async def set_summary(self, session_id: str, text: str) -> None:
        """
        Replace the summary text for the graph at ``session_id``.

        Creates the graph entry if absent (idempotent bootstrap for
        summary-only sessions, though in practice at least one node
        should precede this call). Bumps ``version`` by 1.
        """

    @abstractmethod
    async def prune_node(self, session_id: str, node_id: str) -> bool:
        """
        Mark ``node_id`` as ``NodeStatus.PRUNED`` in the session graph.

        Returns ``True`` when the node was found and its status updated,
        ``False`` when either the session graph or the node does not exist.
        Bumps ``version`` by 1 only on a successful prune.
        """
