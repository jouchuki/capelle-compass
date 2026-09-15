import asyncio, json
from pathlib import Path
from agents.orchestrator import OrchestratorAgent
from memory.store import MemoryStore
from config import OUTPUT_DIR, MUNICIPALITY

async def main():
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    memory = MemoryStore()
    orchestrator = OrchestratorAgent(memory=memory, output_dir=OUTPUT_DIR)
    print(f"[main] Municipality : {MUNICIPALITY}")

    print("[main] Fetching CBS Iv3 data for all years ...")
    await orchestrator._fetcher.run()
    rows = memory.get("parsed_rows") or []
    if rows:
        years = sorted(set(r.get("jaar", "?") for r in rows))
        print(f"[main] Got {len(rows)} rows across years: {years}")
    else:
        print("[main] No data returned from CBS")

    print("[main] Exporting ...")
    await orchestrator._exporter.run()
    print("[main] Visualizing ...")
    await orchestrator._visualizer.run()

    log_path = Path(OUTPUT_DIR) / "run_log.json"
    all_rows = memory.get("parsed_rows") or []
    years = sorted(set(r.get("jaar", "?") for r in all_rows))
    log_path.write_text(json.dumps({"municipality": MUNICIPALITY,
        "rows": len(all_rows), "years": years}, indent=2))
    print(f"[main] Done! Open output/dashboard.html in your browser.")

if __name__ == "__main__":
    asyncio.run(main())
