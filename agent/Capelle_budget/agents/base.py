from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Coroutine
from memory.store import MemoryStore
@dataclass
class Skill:
    name: str
    description: str
    fn: Callable[..., Coroutine[Any, Any, Any]]
    async def __call__(self, *args, **kwargs): return await self.fn(*args, **kwargs)
class BaseAgent(ABC):
    name: str = "base_agent"
    def __init__(self, memory: MemoryStore):
        self.memory = memory
        self.skills: list[Skill] = self._register_skills()
    def _register_skills(self): return []
    def get_skill(self, name): return next((s for s in self.skills if s.name == name), None)
    def skill_manifest(self): return [{"name": s.name, "description": s.description} for s in self.skills]
    @abstractmethod
    async def run(self, *args, **kwargs): ...
    def log(self, msg): print(f"[{self.name}] {msg}")
