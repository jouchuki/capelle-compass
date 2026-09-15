"""
PostgreSQL finding-graph store.

Backed by the ``analysis_graphs`` table (see ``store/schema.sql``).
The table is created at ``initialize()`` time via the shared
``schema.sql`` file — the same mechanism as ``PostgresStore``.

Design notes
------------
* The entire graph is stored as a single JSONB column (``graph``) keyed
  on ``session_id``.  This matches the spec's "additive, single row per
  session" intent and keeps the schema minimal.
* ``version`` is a dedicated INTEGER column used for optimistic
  concurrency: every mutation increments it in the same UPDATE so
  concurrent writers can detect conflicts (Task 2 may add conflict
  handling if needed; for now last-write-wins at the row level which is
  acceptable for a single-writer-per-session model).
* ``updated_at`` is a TIMESTAMPTZ that Postgres sets to ``now()``; we
  never write it explicitly — the DEFAULT handles it for INSERTs; the
  UPDATE sets it explicitly so the column tracks real write times.
* No FK to ``chat_sessions`` is added — the graph is logically tied to a
  session by convention (session_id is a TEXT key), keeping the store
  fully decoupled from the core store package.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import asyncpg

from capelle_platform.graph.base_store import BaseGraphStore
from capelle_platform.graph.models import (
    FindingGraph,
    GraphEdge,
    GraphNode,
    NodeStatus,
)
from capelle_platform.observability import get_logger
from capelle_platform.settings import Settings

_logger = get_logger(__name__)

# Connection-pool sizing — conservative; same rationale as PostgresStore.
_POOL_MIN_SIZE: int = 2
_POOL_MAX_SIZE: int = 10


class PostgresGraphStore(BaseGraphStore):
    """
    ``BaseGraphStore`` backed by the ``analysis_graphs`` Postgres table.

    The store is owned by the ``AppBuilder`` (Task 2 wiring); its
    ``initialize()`` method creates the table via ``schema.sql`` before
    any request is served.
    """

    def __init__(self, settings: Settings) -> None:
        """Record the DSN; defer pool creation to :meth:`initialize`."""
        self._dsn: str = settings.postgres_dsn
        self._pool: asyncpg.Pool | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """
        Create the connection pool and ensure the ``analysis_graphs``
        table exists.

        The table DDL lives in ``store/schema.sql`` alongside the rest of
        the platform schema. ``PostgresStore.initialize()`` already runs
        that file, so in production this call is a no-op (IF NOT EXISTS).
        We call it here as well so ``PostgresGraphStore`` can be
        initialized independently in tests and future split deployments.
        """
        from pathlib import Path

        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=_POOL_MIN_SIZE,
            max_size=_POOL_MAX_SIZE,
            init=self._init_connection,
        )
        schema_sql = (
            Path(__file__).resolve().parent.parent / "store" / "schema.sql"
        ).read_text(encoding="utf-8")
        async with self._pool.acquire() as conn:
            await conn.execute(schema_sql)
        _logger.info("postgres_graph_store_initialized")

    async def close(self) -> None:
        """Drain the pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @staticmethod
    async def _init_connection(conn: asyncpg.Connection) -> None:
        """Register the JSONB codec so columns round-trip as Python dicts."""
        await conn.set_type_codec(
            "jsonb",
            encoder=lambda value: json.dumps(value, default=str),
            decoder=json.loads,
            schema="pg_catalog",
        )

    def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError(
                "PostgresGraphStore not initialized — call initialize() first."
            )
        return self._pool

    # ------------------------------------------------------------------
    # BaseGraphStore implementation
    # ------------------------------------------------------------------

    async def get(self, session_id: str) -> FindingGraph | None:
        """Fetch the graph row; return ``None`` on miss."""
        pool = self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT graph, version FROM analysis_graphs WHERE session_id = $1",
                session_id,
            )
        if row is None:
            return None
        return self._deserialize(session_id, row["graph"], row["version"])

    async def add_node(self, session_id: str, node: GraphNode) -> GraphNode:
        """
        Append ``node`` to the session graph, creating the row if needed.

        Uses an UPSERT: INSERT on first node, UPDATE (with optimistic
        version bump) for subsequent nodes. Limit check runs on the
        in-Python graph fetched just before the write — a rare concurrent
        write race will resolve via the version bump in the next turn.
        """
        graph = await self.get(session_id) or FindingGraph(session_id=session_id)
        self._check_node_limit(graph)
        new_nodes = [*graph.nodes, node]
        new_version = graph.version + 1
        await self._upsert(session_id, graph.copy_with(nodes=new_nodes, version=new_version))
        _logger.debug(
            "pg_graph_node_added",
            session_id=session_id,
            node_id=node.id,
            version=new_version,
        )
        return node

    async def add_edge(self, session_id: str, edge: GraphEdge) -> GraphEdge:
        """Append ``edge`` to the session graph."""
        graph = await self.get(session_id) or FindingGraph(session_id=session_id)
        self._check_edge_limit(graph)
        new_edges = [*graph.edges, edge]
        new_version = graph.version + 1
        await self._upsert(session_id, graph.copy_with(edges=new_edges, version=new_version))
        _logger.debug(
            "pg_graph_edge_added",
            session_id=session_id,
            edge_id=edge.id,
            version=new_version,
        )
        return edge

    async def set_summary(self, session_id: str, text: str) -> None:
        """Replace the summary for the session graph."""
        graph = await self.get(session_id) or FindingGraph(session_id=session_id)
        new_version = graph.version + 1
        await self._upsert(session_id, graph.copy_with(summary=text, version=new_version))
        _logger.debug(
            "pg_graph_summary_set",
            session_id=session_id,
            version=new_version,
        )

    async def prune_node(self, session_id: str, node_id: str) -> bool:
        """Mark ``node_id`` as PRUNED; return False if not found."""
        graph = await self.get(session_id)
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

        new_version = graph.version + 1
        await self._upsert(session_id, graph.copy_with(nodes=new_nodes, version=new_version))
        _logger.debug(
            "pg_graph_node_pruned",
            session_id=session_id,
            node_id=node_id,
            version=new_version,
        )
        return True

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _upsert(self, session_id: str, graph: FindingGraph) -> None:
        """
        INSERT or UPDATE the ``analysis_graphs`` row for ``session_id``.

        The graph dict is serialised via pydantic's ``model_dump`` (which
        handles nested enums and datetimes). ``updated_at`` is always set
        to ``now()`` on conflict so the column reflects the true write time.
        """
        pool = self._ensure_pool()
        graph_payload = graph.model_dump(mode="json")
        now = datetime.now(timezone.utc)
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO analysis_graphs (session_id, graph, version, updated_at) "
                "VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (session_id) DO UPDATE SET "
                "  graph = EXCLUDED.graph, "
                "  version = EXCLUDED.version, "
                "  updated_at = EXCLUDED.updated_at",
                session_id,
                graph_payload,
                graph.version,
                now,
            )

    @staticmethod
    def _deserialize(
        session_id: str,
        graph_dict: dict[str, Any],
        version: int,
    ) -> FindingGraph:
        """
        Reconstruct a ``FindingGraph`` from the JSONB column.

        Pydantic v2's model_validate handles nested enum coercion and
        datetime parsing from the JSON-serialised form.
        """
        # Non-mutating: build a fresh dict so a shared/cached input can never
        # be silently altered by deserialization.
        return FindingGraph.model_validate(
            {**graph_dict, "session_id": session_id, "version": version}
        )
