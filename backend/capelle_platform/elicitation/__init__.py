"""
Elicitation package — mid-run agent question/answer registry.

Provides the abstract base and the default in-memory implementation used
by single-process deployments. Import both from this top-level for
convenience:

    from capelle_platform.elicitation import (
        BaseElicitationRegistry,
        InMemoryElicitationRegistry,
    )
"""

from capelle_platform.elicitation.base_registry import BaseElicitationRegistry
from capelle_platform.elicitation.impl_memory import InMemoryElicitationRegistry

__all__ = [
    "BaseElicitationRegistry",
    "InMemoryElicitationRegistry",
]
