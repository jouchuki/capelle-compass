#!/usr/bin/env python3
"""``capelle-graph`` — record finding-graph nodes, edges, and summaries mid-analysis.

The ohrs analysis agent (and its sub-agents) call this CLI to incrementally
build the live finding-graph for a session.  Each call performs a single HTTP
request to the platform's localhost-only graph endpoints.

Primary path — batch add from a findings file:

    capelle-graph add ./findings.json

Where ``findings.json`` has the shape::

    {
      "nodes": [
        {"ref": "kosten", "type": "finding", "claim": "...", "confidence": "medium",
         "blocks": [...], "citations": [...]},
        {"ref": "grjr",   "type": "context", "claim": "..."}
      ],
      "edges": [
        {"source": "grjr", "target": "kosten", "type": "controls", "rationale": "..."}
      ],
      "summary": "optional single-sentence phase summary"
    }

Refs are local identifiers resolved to server-assigned ids before posting
edges.  A 409 duplicate maps the ref to the existing id (link-don't-duplicate).
An edge source/target that is not a known ref is passed through as-is (real id).

Granular commands (for one-off additions):

    capelle-graph add-node --claim "..." --type finding --confidence medium \\
        --blocks-file ./n.json --citations-file ./c.json --agent-label "lead"

    capelle-graph add-edge --from <id> --to <id> --type causes --rationale "..."

    capelle-graph set-summary --text "Phase 1 done: 3 context nodes added."

    capelle-graph list [--compact]

Environment:
    CAPELLE_MESSAGE_ID    (required) message this run belongs to.
    CAPELLE_INTERNAL_URL  (optional) default http://127.0.0.1:8080.

Exit codes:
    0  success (including duplicate-already-linked for add-node)
    2  usage / file read error
    3  server unreachable / graph feature disabled / all batch items failed

stdlib-only — no third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URL: str = "http://127.0.0.1:8080"
_HTTP_TIMEOUT_SECONDS: int = 30

_EXIT_OK: int = 0
_EXIT_USAGE: int = 2
_EXIT_SERVER: int = 3


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class GraphError(Exception):
    """Transport or server error that should result in a non-zero exit."""


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _request(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any] | str]:
    """
    Perform a single HTTP request.

    Returns a ``(status_code, parsed_body)`` pair.  The body is parsed as
    JSON when possible, else returned as a raw string.  Raises
    :class:`GraphError` on transport failure (connection refused, timeout).
    """
    encoded: bytes | None = None
    headers: dict[str, str] = {}
    if body is not None:
        encoded = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(  # noqa: S310 — fixed localhost scheme
        url,
        data=encoded,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(  # noqa: S310 — localhost only
            req, timeout=_HTTP_TIMEOUT_SECONDS
        ) as resp:
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except urllib.error.URLError as exc:
        raise GraphError(
            f"could not reach platform at {url}: {exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise GraphError(f"timed out waiting for platform at {url}") from exc

    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def _post(
    base_url: str,
    path: str,
    body: dict[str, Any],
) -> tuple[int, dict[str, Any] | str]:
    url = f"{base_url.rstrip('/')}{path}"
    return _request("POST", url, body)


def _get(
    base_url: str,
    path: str,
) -> tuple[int, dict[str, Any] | str]:
    url = f"{base_url.rstrip('/')}{path}"
    return _request("GET", url)


# ---------------------------------------------------------------------------
# Env resolution
# ---------------------------------------------------------------------------


def _resolve_env() -> tuple[str, str]:
    """
    Resolve CAPELLE_MESSAGE_ID and CAPELLE_INTERNAL_URL from environment.

    Returns ``(message_id, base_url)``.  Exits 2 with a clear stderr message
    when CAPELLE_MESSAGE_ID is not set.
    """
    message_id = os.environ.get("CAPELLE_MESSAGE_ID", "").strip()
    if not message_id:
        print(
            "capelle-graph: CAPELLE_MESSAGE_ID is not set — cannot route "
            "to the platform.  (The platform sets this in the agent env.)",
            file=sys.stderr,
        )
        raise SystemExit(_EXIT_USAGE)
    base_url = os.environ.get("CAPELLE_INTERNAL_URL", _DEFAULT_BASE_URL).strip()
    return message_id, base_url


# ---------------------------------------------------------------------------
# Feature-flag check helper
# ---------------------------------------------------------------------------


def _is_flag_disabled(status: int, body: dict[str, Any] | str) -> bool:
    """True when the server returned a 404 with the graph-disabled message."""
    if status != 404:
        return False
    if isinstance(body, dict):
        return "disabled" in str(body.get("error", "")).lower()
    return "disabled" in str(body).lower()


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def _cmd_add_node(args: argparse.Namespace) -> int:
    """
    POST a single node; print ``{"node_id": ...}`` or ``{"duplicate_of": ...}``.
    """
    message_id, base_url = _resolve_env()

    blocks: list[dict[str, Any]] = []
    if args.blocks_file:
        try:
            with open(args.blocks_file, encoding="utf-8") as fh:
                blocks = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"capelle-graph: could not read --blocks-file: {exc}", file=sys.stderr)
            return _EXIT_USAGE

    citations: list[dict[str, Any]] = []
    if args.citations_file:
        try:
            with open(args.citations_file, encoding="utf-8") as fh:
                citations = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"capelle-graph: could not read --citations-file: {exc}",
                file=sys.stderr,
            )
            return _EXIT_USAGE

    payload: dict[str, Any] = {
        "node_type": args.type,
        "claim": args.claim,
        "confidence": args.confidence,
        "status": args.status,
        "blocks": blocks,
        "citations": citations,
        "agent_label": args.agent_label or "",
    }

    path = f"/internal/graph/{message_id}/nodes"
    try:
        status, body = _post(base_url, path, payload)
    except GraphError as exc:
        print(f"capelle-graph: {exc}", file=sys.stderr)
        return _EXIT_SERVER

    if status == 201:
        node_id = body["id"] if isinstance(body, dict) else ""
        print(json.dumps({"node_id": node_id}))
        return _EXIT_OK

    if status == 409 and isinstance(body, dict):
        detail = body.get("detail", {})
        duplicate_of = (
            detail.get("duplicate_of") if isinstance(detail, dict) else None
        )
        print(
            "capelle-graph: node is a near-duplicate; linking to existing "
            f"{duplicate_of}.",
            file=sys.stderr,
        )
        print(json.dumps({"duplicate_of": duplicate_of}))
        return _EXIT_OK

    if _is_flag_disabled(status, body):
        print(
            "capelle-graph: graph feature disabled on server "
            "(CAPELLE_GRAPH_ENABLED=false).",
            file=sys.stderr,
        )
        return _EXIT_SERVER

    detail_str = body if isinstance(body, str) else json.dumps(body)
    print(
        f"capelle-graph: server returned HTTP {status}: {detail_str[:300]}",
        file=sys.stderr,
    )
    return _EXIT_SERVER


def _cmd_add_edge(args: argparse.Namespace) -> int:
    """POST a single directed edge; print the edge JSON on success."""
    message_id, base_url = _resolve_env()

    payload: dict[str, Any] = {
        "source_id": args.from_id,
        "target_id": args.to_id,
        "edge_type": args.type,
        "rationale": args.rationale or "",
    }

    path = f"/internal/graph/{message_id}/edges"
    try:
        status, body = _post(base_url, path, payload)
    except GraphError as exc:
        print(f"capelle-graph: {exc}", file=sys.stderr)
        return _EXIT_SERVER

    if status == 201:
        print(json.dumps(body) if isinstance(body, dict) else body)
        return _EXIT_OK

    if _is_flag_disabled(status, body):
        print(
            "capelle-graph: graph feature disabled on server.",
            file=sys.stderr,
        )
        return _EXIT_SERVER

    detail_str = body if isinstance(body, str) else json.dumps(body)
    print(
        f"capelle-graph: server returned HTTP {status}: {detail_str[:300]}",
        file=sys.stderr,
    )
    return _EXIT_SERVER


def _cmd_set_summary(args: argparse.Namespace) -> int:
    """POST a summary text; exit 0 on 204."""
    message_id, base_url = _resolve_env()

    path = f"/internal/graph/{message_id}/summary"
    try:
        status, body = _post(base_url, path, {"text": args.text})
    except GraphError as exc:
        print(f"capelle-graph: {exc}", file=sys.stderr)
        return _EXIT_SERVER

    if status == 204:
        return _EXIT_OK

    if _is_flag_disabled(status, body):
        print(
            "capelle-graph: graph feature disabled on server.",
            file=sys.stderr,
        )
        return _EXIT_SERVER

    detail_str = body if isinstance(body, str) else json.dumps(body)
    print(
        f"capelle-graph: server returned HTTP {status}: {detail_str[:300]}",
        file=sys.stderr,
    )
    return _EXIT_SERVER


def _cmd_list(args: argparse.Namespace) -> int:
    """GET the current graph and print the JSON."""
    message_id, base_url = _resolve_env()

    path = f"/internal/graph/{message_id}"
    if args.compact:
        path += "?compact=1"

    try:
        status, body = _get(base_url, path)
    except GraphError as exc:
        print(f"capelle-graph: {exc}", file=sys.stderr)
        return _EXIT_SERVER

    if status == 200:
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return _EXIT_OK

    if _is_flag_disabled(status, body):
        print(
            "capelle-graph: graph feature disabled on server.",
            file=sys.stderr,
        )
        return _EXIT_SERVER

    detail_str = body if isinstance(body, str) else json.dumps(body)
    print(
        f"capelle-graph: server returned HTTP {status}: {detail_str[:300]}",
        file=sys.stderr,
    )
    return _EXIT_SERVER


# ---------------------------------------------------------------------------
# Batch add  (the primary path)
# ---------------------------------------------------------------------------


def _post_node_batch_item(
    base_url: str,
    message_id: str,
    node_spec: dict[str, Any],
    ref: str,
) -> tuple[str | None, str, str]:
    """
    POST one node from a batch spec.

    Returns ``(server_id, status_label, error_message)``.

    ``status_label`` is ``"created"`` or ``"linked_existing"``.
    ``server_id`` is None on failure (error_message will be non-empty).
    """
    payload: dict[str, Any] = {
        "node_type": node_spec.get("type", "context"),
        "claim": node_spec.get("claim", ""),
        "confidence": node_spec.get("confidence", "medium"),
        "status": node_spec.get("status", "proposed"),
        "blocks": node_spec.get("blocks", []),
        "citations": node_spec.get("citations", []),
        "agent_label": node_spec.get("agent_label", ""),
    }

    path = f"/internal/graph/{message_id}/nodes"
    try:
        status, body = _post(base_url, path, payload)
    except GraphError as exc:
        return None, "", str(exc)

    if status == 201 and isinstance(body, dict):
        return body.get("id"), "created", ""

    if status == 409 and isinstance(body, dict):
        detail = body.get("detail", {})
        existing_id = (
            detail.get("duplicate_of") if isinstance(detail, dict) else None
        )
        return existing_id, "linked_existing", ""

    detail_str = body if isinstance(body, str) else json.dumps(body)
    return None, "", f"HTTP {status}: {detail_str[:200]}"


def _cmd_batch_add(args: argparse.Namespace) -> int:
    """
    Load a findings.json file and POST nodes then edges in a single pass.

    The file format::

        {
          "nodes": [{"ref": "local-name", "type": "...", "claim": "...", ...}],
          "edges": [{"source": "ref-or-id", "target": "ref-or-id",
                     "type": "...", "rationale": "..."}],
          "summary": "optional"
        }

    Ref-to-id resolution happens client-side.  On 409 the ref is mapped to
    the ``duplicate_of`` id so subsequent edges still connect correctly.
    Unknown refs in edges are passed through as-is (treated as real server ids).

    Output::

        {
          "nodes": {"<ref>": {"id": "<server-id>", "status": "created|linked_existing"}},
          "edges": [{"source": "<id>", "target": "<id>", "edge_type": "...",
                     "status": "created|error", "error": "..."}],
          "errors": ["<ref>: <reason>", ...]
        }

    Exit 0 if anything succeeded; exit 3 if everything failed.
    """
    message_id, base_url = _resolve_env()

    # --- load file ----------------------------------------------------------
    findings_path: str = args.file
    try:
        with open(findings_path, encoding="utf-8") as fh:
            doc: dict[str, Any] = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"capelle-graph: could not read {findings_path!r}: {exc}",
            file=sys.stderr,
        )
        return _EXIT_USAGE

    if not isinstance(doc, dict):
        print(
            "capelle-graph: findings file must be a JSON object.",
            file=sys.stderr,
        )
        return _EXIT_USAGE

    nodes_spec: list[dict[str, Any]] = doc.get("nodes") or []
    edges_spec: list[dict[str, Any]] = doc.get("edges") or []
    summary_text: str | None = doc.get("summary") or None

    # --- POST nodes ---------------------------------------------------------
    # ref → server_id mapping; populated as nodes are created or linked.
    ref_to_id: dict[str, str] = {}
    node_results: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    any_success = False

    for node_spec in nodes_spec:
        ref: str = str(node_spec.get("ref") or "")
        if not ref:
            errors.append("node missing 'ref' field — skipped")
            continue

        server_id, status_label, error_msg = _post_node_batch_item(
            base_url, message_id, node_spec, ref
        )

        if server_id is not None:
            ref_to_id[ref] = server_id
            node_results[ref] = {"id": server_id, "status": status_label}
            any_success = True
        else:
            errors.append(f"node '{ref}': {error_msg}")

    # --- POST edges ---------------------------------------------------------
    edge_results: list[dict[str, str]] = []

    for edge_spec in edges_spec:
        raw_source: str = str(edge_spec.get("source") or "")
        raw_target: str = str(edge_spec.get("target") or "")
        edge_type: str = str(edge_spec.get("type") or "")
        rationale: str = str(edge_spec.get("rationale") or "")

        # Resolve refs; pass unknown source/targets through as real ids.
        resolved_source = ref_to_id.get(raw_source, raw_source)
        resolved_target = ref_to_id.get(raw_target, raw_target)

        payload: dict[str, Any] = {
            "source_id": resolved_source,
            "target_id": resolved_target,
            "edge_type": edge_type,
            "rationale": rationale,
        }

        path = f"/internal/graph/{message_id}/edges"
        try:
            status, body = _post(base_url, path, payload)
        except GraphError as exc:
            edge_results.append(
                {
                    "source": resolved_source,
                    "target": resolved_target,
                    "edge_type": edge_type,
                    "status": "error",
                    "error": str(exc),
                }
            )
            errors.append(
                f"edge {raw_source!r}→{raw_target!r}: transport error: {exc}"
            )
            continue

        if status == 201:
            edge_id = body.get("id", "") if isinstance(body, dict) else ""
            edge_results.append(
                {
                    "source": resolved_source,
                    "target": resolved_target,
                    "edge_type": edge_type,
                    "status": "created",
                    "id": edge_id,
                }
            )
            any_success = True
        else:
            detail_str = body if isinstance(body, str) else json.dumps(body)
            edge_results.append(
                {
                    "source": resolved_source,
                    "target": resolved_target,
                    "edge_type": edge_type,
                    "status": "error",
                    "error": f"HTTP {status}: {detail_str[:200]}",
                }
            )
            errors.append(
                f"edge {raw_source!r}→{raw_target!r}: HTTP {status}: "
                f"{detail_str[:150]}"
            )

    # --- POST summary -------------------------------------------------------
    if summary_text:
        path = f"/internal/graph/{message_id}/summary"
        try:
            status, body = _post(base_url, path, {"text": summary_text})
            if status == 204:
                any_success = True
            else:
                detail_str = body if isinstance(body, str) else json.dumps(body)
                errors.append(
                    f"summary: HTTP {status}: {detail_str[:150]}"
                )
        except GraphError as exc:
            errors.append(f"summary: {exc}")

    # --- output -------------------------------------------------------------
    result: dict[str, Any] = {
        "nodes": node_results,
        "edges": edge_results,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not any_success and (nodes_spec or edges_spec):
        return _EXIT_SERVER
    return _EXIT_OK


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capelle-graph",
        description=(
            "Record finding-graph nodes, edges, and summaries during analysis.  "
            "Reads CAPELLE_MESSAGE_ID and CAPELLE_INTERNAL_URL from the environment."
        ),
    )
    sub = parser.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")
    sub.required = True

    # -- add (batch) ---------------------------------------------------------
    p_add = sub.add_parser(
        "add",
        help="Batch-add nodes + edges from a findings JSON file.",
    )
    p_add.add_argument(
        "file",
        metavar="FINDINGS_FILE",
        help=(
            "Path to a JSON file with 'nodes', 'edges', and optional 'summary' keys.  "
            "Nodes carry a local 'ref' that edges use for source/target resolution."
        ),
    )

    # -- add-node ------------------------------------------------------------
    p_node = sub.add_parser("add-node", help="Add a single graph node.")
    p_node.add_argument("--claim", required=True, help="One-sentence claim (≤280 chars).")
    p_node.add_argument(
        "--type",
        required=True,
        choices=["finding", "context", "hypothesis", "verification"],
        help="Node type.",
    )
    p_node.add_argument(
        "--confidence",
        required=True,
        choices=["low", "medium", "high"],
        help="Confidence level.",
    )
    p_node.add_argument(
        "--status",
        default="proposed",
        choices=["proposed", "supported", "verified", "pruned"],
        help="Node status (default: proposed).",
    )
    p_node.add_argument(
        "--blocks-file",
        dest="blocks_file",
        default=None,
        metavar="PATH",
        help="JSON file containing a list of v2 Block dicts.",
    )
    p_node.add_argument(
        "--citations-file",
        dest="citations_file",
        default=None,
        metavar="PATH",
        help="JSON file containing a list of v2 CitationRef dicts.",
    )
    p_node.add_argument(
        "--agent-label",
        dest="agent_label",
        default="",
        metavar="LABEL",
        help="Free-text label identifying the agent or sub-agent.",
    )

    # -- add-edge ------------------------------------------------------------
    p_edge = sub.add_parser("add-edge", help="Add a directed edge between two nodes.")
    p_edge.add_argument("--from", dest="from_id", required=True, metavar="ID",
                        help="Source node id.")
    p_edge.add_argument("--to",   dest="to_id",   required=True, metavar="ID",
                        help="Target node id.")
    p_edge.add_argument(
        "--type",
        required=True,
        choices=["causes", "explains", "controls", "tensions_with",
                 "depends_on", "decomposes_into"],
        help="Edge type.",
    )
    p_edge.add_argument(
        "--rationale",
        default="",
        help=(
            "One-sentence rationale.  For 'causes' edges MUST name the "
            "counterfactual (comparison + assumption)."
        ),
    )

    # -- set-summary ---------------------------------------------------------
    p_sum = sub.add_parser("set-summary", help="Set or replace the session graph summary.")
    p_sum.add_argument("--text", required=True, help="One- to three-sentence phase summary.")

    # -- list ----------------------------------------------------------------
    p_list = sub.add_parser("list", help="Print the current graph as JSON.")
    p_list.add_argument(
        "--compact",
        action="store_true",
        help="Return compact shape (id, claim, node_type per node; edges as triples).",
    )

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    dispatch = {
        "add": _cmd_batch_add,
        "add-node": _cmd_add_node,
        "add-edge": _cmd_add_edge,
        "set-summary": _cmd_set_summary,
        "list": _cmd_list,
    }
    handler = dispatch.get(args.subcommand)
    if handler is None:
        parser.print_help(sys.stderr)
        return _EXIT_USAGE
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
