"""
GraphService — orchestration layer for the finding-graph.

Sits between the HTTP handlers and the BaseGraphStore. Responsibilities:

1. **Dedup guard** — normalises the incoming claim (lowercase, strip
   punctuation, collapse whitespace) and compares it against all non-pruned
   claims in the session graph using ``difflib.SequenceMatcher``. A ratio
   >= ``DEDUP_RATIO`` raises ``DuplicateNodeError`` carrying the existing
   node's id so the caller can return HTTP 409 with a helpful body.

2. **Endpoint-existence check** — ``add_edge`` verifies that both
   ``source_id`` and ``target_id`` already exist in the session graph (as
   non-pruned nodes) before delegating to the store. Unknown ids raise
   ``UnknownNodeError`` (→ HTTP 404).

3. **WS broadcast** — after every successful mutation the service invokes
   the ``broadcast`` callable with a ``graph_delta`` event. The callable is
   injected at construction time (dependency-injected for testability); the
   builder passes ``ws_hub.broadcast_to_user(user_id, event)`` wrapped in a
   partial that fixes the user_id — OR the handler resolves user_id and
   calls broadcast(event) directly. The service itself receives a single-arg
   coroutine ``broadcast(event: dict) -> None``.

Structured logging via the platform's ``StructuredLogger``; no ``print``.
All limits / thresholds come from ``constants.py`` — no magic numbers here.
"""

from __future__ import annotations

import difflib
import re
import string
from collections.abc import Awaitable, Callable
from typing import Any

from capelle_platform.graph.base_store import BaseGraphStore
from capelle_platform.graph.constants import DEDUP_RATIO
from capelle_platform.graph.models import (
    FindingGraph,
    GraphEdge,
    GraphNode,
    NodeStatus,
)
from capelle_platform.observability import get_logger
from capelle_platform.observability.logger import TraceContext

_logger = get_logger(__name__)

# Type alias: a broadcast coroutine.
#
# The callable receives the full event dict; the caller (builder) wires
# it to a closure that knows how to route to the correct user (e.g.
# ``ws_hub.broadcast_to_user`` wrapped around a session→user lookup).
# Tests inject a simple ``AsyncMock`` that captures the event for assertion.
_BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]

# Punctuation table for claim normalisation.
_PUNCT_TABLE = str.maketrans("", "", string.punctuation)


class DuplicateNodeError(Exception):
    """
    Raised by ``GraphService.add_node`` when the incoming claim is too
    similar to an existing non-pruned node in the same session.

    ``existing_id`` carries the id of the first matching node so the HTTP
    handler can return ``{"detail": {"duplicate_of": existing_id}}``.
    """

    def __init__(self, existing_id: str, ratio: float) -> None:
        self.existing_id = existing_id
        self.ratio = ratio
        super().__init__(
            f"Duplicate node detected (ratio={ratio:.3f}); "
            f"existing node id='{existing_id}'."
        )


class UnknownNodeError(Exception):
    """
    Raised by ``GraphService.add_edge`` when an endpoint node id does not
    exist in the session graph (or has been pruned).

    ``node_id`` carries the id that was not found so the HTTP handler can
    return a descriptive 404 body.
    """

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        super().__init__(
            f"Node id='{node_id}' not found in session graph. "
            "Both edge endpoints must exist before an edge is added."
        )


def _normalise_claim(claim: str) -> str:
    """
    Normalise a claim string for dedup comparison.

    Steps (order matters):
    1. Lowercase.
    2. Strip punctuation (translate with a punctuation-removal table).
    3. Collapse all whitespace runs to a single space and strip.
    """
    lowered = claim.lower()
    stripped = lowered.translate(_PUNCT_TABLE)
    return re.sub(r"\s+", " ", stripped).strip()


class GraphService:
    """
    Orchestration service for the finding-graph.

    Wires together the ``BaseGraphStore`` (persistence), the dedup guard,
    and the WS broadcast callable.

    Args:
        store: The concrete graph store (InMemory for tests, Postgres for prod).
        broadcast: Async callable ``(event: dict) -> None`` that delivers
            ``graph_delta`` events to the session's WebSocket subscribers.
            The caller is responsible for fixing the audience (e.g. via a
            partial over ``ws_hub.broadcast_to_user``); the service just
            fires-and-awaits.
    """

    def __init__(
        self,
        store: BaseGraphStore,
        broadcast: _BroadcastFn,
    ) -> None:
        self._store = store
        self._broadcast = broadcast

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def add_node(
        self,
        session_id: str,
        message_id: str,
        node: GraphNode,
    ) -> GraphNode:
        """
        Add a node to the session graph after running the dedup guard.

        Workflow:
        1. Fetch the current session graph (None → no prior nodes, no dedup risk).
        2. Normalise the incoming claim and compare against all non-pruned
           existing claims. Raise ``DuplicateNodeError`` on first match.
        3. Delegate to the store (which enforces node limits).
        4. Broadcast ``graph_delta`` with ``kind="node"``.

        Args:
            session_id: Target session.
            message_id: The running agent's message id (included in the event).
            node: Fully-constructed ``GraphNode`` (id already set by the
                  handler via ``generate_id()``; the service does not mint ids).

        Returns:
            The stored node (identical to the input for the in-memory store).

        Raises:
            DuplicateNodeError: If a near-identical claim already exists.
            LimitExceededError: If the session node cap is reached.
        """
        trace_id = TraceContext.get()

        graph = await self._store.get(session_id)
        if graph is not None:
            self._check_dedup(graph, node.claim, trace_id, session_id)

        stored = await self._store.add_node(session_id, node)

        await self._emit(
            session_id=session_id,
            message_id=message_id,
            kind="node",
            data=stored.model_dump(mode="json"),
        )

        _logger.info(
            "graph_service_node_added",
            session_id=session_id,
            message_id=message_id,
            node_id=stored.id,
            node_type=stored.node_type.value,
            trace_id=trace_id,
        )
        return stored

    async def add_edge(
        self,
        session_id: str,
        message_id: str,
        edge: GraphEdge,
    ) -> GraphEdge:
        """
        Add an edge to the session graph after validating endpoint existence.

        Workflow:
        1. Fetch the current graph.
        2. Verify ``source_id`` and ``target_id`` both exist as non-pruned
           nodes. Raise ``UnknownNodeError`` on first missing id.
        3. Delegate to the store (which enforces edge limits).
        4. Broadcast ``graph_delta`` with ``kind="edge"``.

        Args:
            session_id: Target session.
            message_id: The running agent's message id.
            edge: Fully-constructed ``GraphEdge``.

        Returns:
            The stored edge.

        Raises:
            UnknownNodeError: If either endpoint is absent or pruned.
            LimitExceededError: If the session edge cap is reached.
        """
        trace_id = TraceContext.get()

        graph = await self._store.get(session_id)
        self._check_endpoints(graph, edge.source_id, edge.target_id, session_id)

        stored = await self._store.add_edge(session_id, edge)

        await self._emit(
            session_id=session_id,
            message_id=message_id,
            kind="edge",
            data=stored.model_dump(mode="json"),
        )

        _logger.info(
            "graph_service_edge_added",
            session_id=session_id,
            message_id=message_id,
            edge_id=stored.id,
            edge_type=stored.edge_type.value,
            trace_id=trace_id,
        )
        return stored

    async def set_summary(
        self,
        session_id: str,
        message_id: str,
        text: str,
    ) -> None:
        """
        Replace the session graph's summary text.

        Delegates to the store, then broadcasts ``graph_delta`` with
        ``kind="summary"`` and ``data=<the text string>``.

        Args:
            session_id: Target session.
            message_id: The running agent's message id.
            text: New summary text (replaces any prior summary).
        """
        trace_id = TraceContext.get()

        await self._store.set_summary(session_id, text)

        await self._emit(
            session_id=session_id,
            message_id=message_id,
            kind="summary",
            data=text,
        )

        _logger.info(
            "graph_service_summary_set",
            session_id=session_id,
            message_id=message_id,
            summary_len=len(text),
            trace_id=trace_id,
        )

    async def get(self, session_id: str) -> FindingGraph | None:
        """
        Return the full ``FindingGraph`` for ``session_id``, or ``None``.
        """
        return await self._store.get(session_id)

    async def get_compact(
        self, session_id: str
    ) -> dict[str, Any] | None:
        """
        Return a compact representation of the session graph.

        Shape::

            {
                "nodes": [{"id": str, "claim": str, "node_type": str}, ...],
                "edges": [[source_id, target_id, edge_type], ...]
            }

        Returns ``None`` when no graph exists for the session.  Intended for
        the ``capelle-graph list --compact`` command and the dedup-before-add
        pattern (agents scan this before calling add-node).
        """
        graph = await self._store.get(session_id)
        if graph is None:
            return None

        compact_nodes = [
            {
                "id": n.id,
                "claim": n.claim,
                "node_type": n.node_type.value,
            }
            for n in graph.nodes
        ]
        compact_edges = [
            (e.source_id, e.target_id, e.edge_type.value)
            for e in graph.edges
        ]
        return {"nodes": compact_nodes, "edges": compact_edges}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_dedup(
        self,
        graph: FindingGraph,
        incoming_claim: str,
        trace_id: str,
        session_id: str,
    ) -> None:
        """
        Compare ``incoming_claim`` against all non-pruned claims in ``graph``.

        Raises ``DuplicateNodeError`` on the first match whose normalised
        similarity ratio is >= ``DEDUP_RATIO``.
        """
        normalised_incoming = _normalise_claim(incoming_claim)

        for node in graph.nodes:
            if node.status == NodeStatus.PRUNED:
                continue

            normalised_existing = _normalise_claim(node.claim)
            ratio = difflib.SequenceMatcher(
                None, normalised_incoming, normalised_existing
            ).ratio()

            if ratio >= DEDUP_RATIO:
                _logger.info(
                    "graph_service_dedup_triggered",
                    session_id=session_id,
                    existing_id=node.id,
                    ratio=f"{ratio:.3f}",
                    trace_id=trace_id,
                )
                raise DuplicateNodeError(existing_id=node.id, ratio=ratio)

    @staticmethod
    def _check_endpoints(
        graph: FindingGraph | None,
        source_id: str,
        target_id: str,
        session_id: str,
    ) -> None:
        """
        Raise ``UnknownNodeError`` if either endpoint is absent in ``graph``.

        A ``None`` graph (no nodes yet) means every endpoint check fails.
        Pruned nodes are NOT valid endpoints — they are logically absent.
        """
        if graph is None:
            raise UnknownNodeError(source_id)

        active_ids: frozenset[str] = frozenset(
            n.id for n in graph.nodes if n.status != NodeStatus.PRUNED
        )

        if source_id not in active_ids:
            raise UnknownNodeError(source_id)
        if target_id not in active_ids:
            raise UnknownNodeError(target_id)

    async def _emit(
        self,
        session_id: str,
        message_id: str,
        kind: str,
        data: Any,
    ) -> None:
        """
        Build a ``graph_delta`` event and call the broadcast callable.

        Failures are logged and swallowed — a dropped WS event must never
        surface as an HTTP error to the agent.
        """
        event: dict[str, Any] = {
            "type": "graph_delta",
            "session_id": session_id,
            "message_id": message_id,
            "kind": kind,
            "data": data,
        }
        try:
            await self._broadcast(event)
        except Exception:  # noqa: BLE001
            _logger.exception(
                "graph_service_broadcast_failed",
                session_id=session_id,
                message_id=message_id,
                kind=kind,
            )
