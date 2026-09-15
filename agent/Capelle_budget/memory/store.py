from typing import Any
class MemoryStore:
    def __init__(self): self._store: dict[str, Any] = {}
    def set(self, key, value): self._store[key] = value
    def get(self, key, default=None): return self._store.get(key, default)
    def append(self, key, item):
        existing = self._store.get(key, [])
        existing.append(item)
        self._store[key] = existing
    def keys(self): return list(self._store.keys())
