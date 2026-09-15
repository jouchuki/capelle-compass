#!/usr/bin/env python3
"""``capelle-ask`` — ask the END USER a question mid-analysis, and block for it.

The ohrs analysis agent calls this CLI when it needs the user to disambiguate
intent (scope, depth, format, which sources). It performs a single blocking
HTTP POST to the platform's localhost-only elicitation endpoint; the platform
pushes the question to the user's browser over WebSocket and holds the request
open until the user answers (or a server-side timeout fires). The user's answer
is printed to stdout for the agent to read as tool output.

Usage:
    capelle-ask --question "Wilt u een kort overzicht of een diepgaand rapport?" \
        --option "Kort overzicht" --option "Diepgaand rapport" --allow-free-text

Environment:
    CAPELLE_MESSAGE_ID   (required) the assistant message this analysis belongs
                         to; the platform sets it in the agent's env.
    CAPELLE_INTERNAL_URL (optional) base URL of the platform's internal API;
                         default http://127.0.0.1:8080.

Exit codes:
    0  answer printed to stdout (empty string if the user did not answer in time
       — the agent should then proceed with a sensible default).
    1  misconfiguration or transport/HTTP error (message on stderr).

This helper is bundled in Compass under agent/Capelle_report. It talks to
POST /internal/ask/{message_id} in the Compass backend (InternalHandler.ask).
It uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# The platform's server-side elicitation timeout is elicitation_timeout_seconds
# (default 300s). We wait a little longer so the server's own timeout response
# reaches us instead of our socket giving up first.
_CLIENT_TIMEOUT_SECONDS = 330
_DEFAULT_BASE_URL = "http://127.0.0.1:8080"


class AskError(Exception):
    """A misconfiguration or transport failure that should exit non-zero."""


def _post_question(
    base_url: str,
    message_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    url = f"{base_url.rstrip('/')}/internal/ask/{message_id}"
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 — fixed localhost scheme
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(  # noqa: S310 — localhost only
            request, timeout=_CLIENT_TIMEOUT_SECONDS
        ) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise AskError(f"platform returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise AskError(f"could not reach platform at {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise AskError(f"timed out waiting for the platform at {url}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AskError(f"platform returned non-JSON response: {raw[:200]!r}") from exc
    if not isinstance(parsed, dict):
        raise AskError(f"platform returned unexpected JSON: {parsed!r}")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="capelle-ask",
        description="Ask the user a question mid-analysis and block for the answer.",
    )
    parser.add_argument(
        "--question",
        required=True,
        help="The question to show the user (Dutch).",
    )
    parser.add_argument(
        "--option",
        action="append",
        default=[],
        dest="options",
        help="A predefined answer option (repeatable). May be omitted.",
    )
    parser.add_argument(
        "--allow-free-text",
        action="store_true",
        help="Let the user type a free-text answer in addition to the options.",
    )
    args = parser.parse_args(argv)

    message_id = os.environ.get("CAPELLE_MESSAGE_ID", "").strip()
    if not message_id:
        print(
            "capelle-ask: CAPELLE_MESSAGE_ID is not set — cannot route the "
            "question. (The platform sets this in the agent env.)",
            file=sys.stderr,
        )
        return 1

    base_url = os.environ.get("CAPELLE_INTERNAL_URL", _DEFAULT_BASE_URL).strip()
    payload: dict[str, object] = {
        "question": args.question,
        "options": list(args.options),
        "allow_free_text": bool(args.allow_free_text),
    }

    try:
        result = _post_question(base_url, message_id, payload)
    except AskError as exc:
        print(f"capelle-ask: {exc}", file=sys.stderr)
        return 1

    answer = result.get("answer")
    if not isinstance(answer, str):
        answer = ""
    if result.get("timed_out"):
        print(
            "capelle-ask: the user did not answer in time; proceeding with no "
            "answer. Choose a sensible default.",
            file=sys.stderr,
        )
    # The agent reads stdout as the tool result — print ONLY the answer there.
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
