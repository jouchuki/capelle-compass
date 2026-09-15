#!/usr/bin/env python3
"""
PostToolUse hook script for openharness-rs.

Intercepts Bash tool outputs, detects Capelle CLI tool calls with --output-json,
and saves the structured ToolOutput to capelle_rag/analyses/ for dashboard consumption.

Called by openharness-rs with the hook payload as $ARGUMENTS (JSON).
Environment: OPENHARNESS_HOOK_EVENT, OPENHARNESS_HOOK_PAYLOAD
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ANALYSES_DIR = Path(__file__).parent / "analyses"
CAPELLE_TOOLS = ["cbs ", "capelle-budget ", "capelle-beleid ", "capelle-buitenbeter "]


def main():
    # Get payload from environment or stdin
    payload_str = os.environ.get("OPENHARNESS_HOOK_PAYLOAD", "")
    if not payload_str and len(sys.argv) > 1:
        payload_str = sys.argv[1]
    if not payload_str:
        try:
            payload_str = sys.stdin.read()
        except Exception:
            return

    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError:
        return

    # Only process Bash tool results
    tool_name = payload.get("tool_name", "")
    if tool_name != "Bash":
        return

    # Check if it's a capelle tool call with --output-json
    command = payload.get("input", {}).get("command", "")
    if not any(tool in command for tool in CAPELLE_TOOLS):
        return
    if "--output-json" not in command:
        return

    # Extract the output
    output = payload.get("output", "")
    if not output:
        return

    # Try to parse as ToolOutput JSON
    try:
        tool_output = json.loads(output)
    except json.JSONDecodeError:
        return

    # Validate it looks like a ToolOutput
    if "tool" not in tool_output or "data" not in tool_output:
        return

    # Save to analyses directory
    ANALYSES_DIR.mkdir(parents=True, exist_ok=True)
    tool = tool_output.get("tool", "unknown")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"tool_output_{tool}_{ts}.json"
    path = ANALYSES_DIR / filename
    path.write_text(json.dumps(tool_output, ensure_ascii=False, indent=2), encoding="utf-8")

    # Print confirmation to stderr (doesn't interfere with tool output)
    print(f"[capelle] Saved tool output → {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
