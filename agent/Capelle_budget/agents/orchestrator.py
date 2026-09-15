from __future__ import annotations
from datetime import datetime
from agents.base import BaseAgent
from agents.cbs_fetcher import CBSFetcherAgent
from agents.exporter import ExporterAgent
from agents.visualizer import VisualizerAgent
from memory.store import MemoryStore
from config import OUTPUT_DIR, MUNICIPALITY

class OrchestratorAgent(BaseAgent):
    name = "orchestrator"
    def __init__(self, memory, output_dir=OUTPUT_DIR):
        super().__init__(memory); self.output_dir = output_dir
        self._fetcher    = CBSFetcherAgent(memory)
        self._exporter   = ExporterAgent(memory, output_dir=output_dir)
        self._visualizer = VisualizerAgent(memory, output_dir=output_dir)

    def _register_skills(self): return []

    async def run(self, start_url=None):
        self.log(f"Municipality: {MUNICIPALITY}")
        for agent in (self._fetcher, self._exporter, self._visualizer):
            for s in agent.skill_manifest():
                self.log(f"  {agent.name}.{s['name']}: {s['description']}")
        started_at = datetime.utcnow().isoformat()
        self.log("Step 1: Fetch real data from CBS Iv3 API")
        fetch  = await self._fetcher.run()
        self.log("Step 2: Export")
        export = await self._exporter.run()
        self.log("Step 3: Visualize")
        viz    = await self._visualizer.run()
        return {"started_at": started_at, "finished_at": datetime.utcnow().isoformat(),
                "municipality": MUNICIPALITY, "fetch": fetch, "export": export, "visualize": viz}
