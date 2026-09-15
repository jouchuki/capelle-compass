"""
Named constants for the finding-graph domain.

All hard limits and tuning parameters live here — no magic numbers anywhere
else in the graph package.
"""

from __future__ import annotations

from typing import Final

# Maximum character length for a node claim (one-sentence constraint).
MAX_CLAIM_LEN: Final[int] = 280

# Per-session node cap. Exceeding this raises LimitExceededError.
MAX_NODES_PER_SESSION: Final[int] = 300

# Per-session edge cap. Exceeding this raises LimitExceededError.
MAX_EDGES_PER_SESSION: Final[int] = 1200

# Maximum number of v2 Block dicts allowed on a single node.
MAX_BLOCKS_PER_NODE: Final[int] = 30

# Normalized-claim similarity threshold for the server-side dedup guard.
# Claims whose difflib ratio >= DEDUP_RATIO are treated as duplicates.
DEDUP_RATIO: Final[float] = 0.90

# Expected length (in hex characters) of a server-assigned node ID.
NODE_ID_HEX_LEN: Final[int] = 12
