"""
Utility module — the only place standalone functions are permitted.

All stateless helpers that don't belong to a specific domain class live here.
"""

from __future__ import annotations

import uuid


def generate_id() -> str:
    """Generate a 12-character hex ID suitable for analysis and user records."""
    return uuid.uuid4().hex[:12]
