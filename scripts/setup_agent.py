"""Install Compass's bundled research tools into a dedicated environment."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import venv


PACKAGES = (
    "cbs_tool",
    "Capelle_beleid",
    "Capelle_budget",
    "Capelle_buitenbeter",
    "Capelle_cube",
    "Capelle_groeikernen",
    "Capelle_report",
    "jeugdzorg",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extras", action="store_true", help="Also install scraper and dashboard dependencies")
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        parser.error("Python 3.12 or newer is required")
    root = Path(__file__).resolve().parents[1]
    env = root / ".venv-agent"
    if not (env / "bin/python").exists():
        venv.EnvBuilder(with_pip=True).create(env)
    python = str(env / "bin/python")
    command = [python, "-m", "pip", "install"]
    for package in PACKAGES:
        command.extend(["-e", str(root / "agent" / package)])
    subprocess.run(command, check=True)
    if args.extras:
        subprocess.run(
            [python, "-m", "pip", "install", "-r", str(root / "agent/requirements-extra.txt")],
            check=True,
        )
    # Provider configuration is supplied by the operator. Never copy credentials
    # or overwrite an existing backend .env as part of tool installation.
    (root / ".agent-home/.openharnessrs").mkdir(parents=True, exist_ok=True)
    print("Installed Compass agent tools in .venv-agent/bin.")
    print("Run make check-agent, then configure OHRS and data using docs/runtime.md.")


if __name__ == "__main__":
    main()
