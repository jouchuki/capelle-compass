from __future__ import annotations
import json, csv
from pathlib import Path
from agents.base import BaseAgent, Skill
from config import OUTPUT_DIR
class ExporterAgent(BaseAgent):
    name = "exporter"
    def __init__(self, memory, output_dir=OUTPUT_DIR):
        self.output_dir = Path(output_dir); super().__init__(memory)
    def _register_skills(self):
        return [
            Skill("export_csv",  "Write parsed rows to a CSV file.",  self._export_csv),
            Skill("export_json", "Write parsed rows to a JSON file.", self._export_json),
        ]
    async def run(self):
        rows = self.memory.get("parsed_rows") or []
        if not rows: return {"status": "ok", "rows_exported": 0, "files": []}
        csv_path = await self.get_skill("export_csv")(rows)
        json_path = await self.get_skill("export_json")(rows)
        files = [str(p) for p in [csv_path, json_path] if p]
        return {"status": "ok", "rows_exported": len(rows), "files": files}
    async def _export_csv(self, rows):
        path = self.output_dir / "budget.csv"
        try:
            import pandas as pd; pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
        except ImportError:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
        self.log(f"Wrote {path}"); return path
    async def _export_json(self, rows):
        path = self.output_dir / "budget.json"
        path.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
        self.log(f"Wrote {path}"); return path
