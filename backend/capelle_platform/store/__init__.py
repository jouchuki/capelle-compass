"""Public API for the persistence layer."""

from capelle_platform.store.base_store import BaseStore
from capelle_platform.store.impl_sqlite import SQLiteStore

__all__: list[str] = ["BaseStore", "SQLiteStore"]
