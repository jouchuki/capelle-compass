"""Check the bundled research workspace and installed CLI entry points offline."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile


COMMANDS = (
    "cbs", "capelle-beleid", "capelle-budget", "capelle-buitenbeter",
    "capelle-cube", "groeikernen", "capelle-report", "capelle-ask",
    "capelle-graph", "capelle-jeugdzorg",
)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    agent = root / "agent"
    errors: list[str] = []
    required = [
        agent / "skills" / name for name in (
            "beleid.md", "budget.md", "bewonersenquete.md", "capelle-analyse.md",
            "cbs.md", "cube.md", "groeikernen.md", "fan-out.md", "graph.md",
        )
    ]
    required += [agent / "jeugdzorg/skills/capelle-jeugdzorg.md"]
    required += [agent / "jeugdzorg" / name for name in (
        "synthetic_jeugdzorg_2020_2025_clean.csv",
        "synthetic_jeugdzorg_2020_2025.csv",
        "synthetic_jeugdzorg_2020_2025.orig.csv",
    )]
    required += [agent / "reference/cbs/municipalities.json"]
    for path in required:
        if not path.is_file():
            errors.append(f"Missing asset: {path.relative_to(root)}")
    for workspace in (agent, agent / "jeugdzorg"):
        plugin = workspace / ".openharnessrs/plugins/capelle"
        try:
            manifest = json.loads((plugin / "plugin.json").read_text())
            if manifest.get("name") != "capelle" or (plugin / "skills").resolve() != (workspace / "skills").resolve():
                errors.append(f"Invalid plugin layout: {plugin.relative_to(root)}")
        except (OSError, ValueError) as exc:
            errors.append(f"Invalid plugin manifest: {exc}")
    # Unrelated cwd catches imports and data paths that accidentally rely on
    # launching the CLI from its original source directory.
    with tempfile.TemporaryDirectory(prefix="compass-tools-check-") as cwd:
        for command in COMMANDS:
            try:
                result = subprocess.run(
                    [str(root / ".venv-agent/bin" / command), "--help"],
                    cwd=cwd, capture_output=True, text=True, timeout=60,
                )
                if result.returncode:
                    errors.append(f"{command}: {result.stderr.strip() or result.stdout.strip()}")
            except (OSError, subprocess.TimeoutExpired) as exc:
                errors.append(f"{command}: {exc}")
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print(f"PASS: both skill workspaces, bundled reference fixtures, and {len(COMMANDS)} CLI entry points.")
    print("OHRS/provider access, Chroma, and national corpus coverage require separate setup and live checks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
