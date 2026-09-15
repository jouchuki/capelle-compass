"""
Finding-graph domain package.

Exports the public surface for the graph layer:
  - constants: hard limits and tuning parameters
  - models: pydantic domain types (GraphNode, GraphEdge, FindingGraph)
  - base_store: BaseGraphStore ABC + LimitExceededError
  - impl_memory: InMemoryGraphStore (test / single-process)
  - impl_postgres: PostgresGraphStore (production)
"""

from __future__ import annotations

from capelle_platform.graph import constants, models, base_store

__all__: list[str] = [
    "constants",
    "models",
    "base_store",
]
