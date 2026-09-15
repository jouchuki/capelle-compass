from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from capelle_platform.observability.logger import get_logger
from capelle_platform.observability.types import JobMeta

_logger = get_logger(__name__)

_LEVEL_DEFAULT = "DEFAULT"
_LEVEL_ERROR = "ERROR"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# Trajectory schema this mapper natively understands after normalization. OHRS
# stamps every line with "v" (see oh crates/oh-harness/src/trajectory.rs); a bump
# means the line shapes changed and _normalize_rows must be revisited.
_SUPPORTED_TRAJECTORY_VERSION = 1


def _normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize OHRS's kind-tagged trajectory schema to the legacy role/content
    shape the rest of this module consumes.

    OHRS migrated trajectory.jsonl from ``{role, content, _meta}`` to a
    ``{v, kind, ...}`` event schema. Rather than rewrite the mapper, we adapt at
    the input boundary: ``meta``→system, ``assistant``→assistant (``text``→
    ``content``, tool_calls reshaped to the OpenAI ``function`` nesting), ``usage``
    folded into its turn's assistant ``_meta.usage``, ``tool_result``→tool,
    ``end`` dropped. Legacy rows (already carrying ``role``) pass through
    untouched, so old fixtures and any un-migrated backlog still map.
    """
    if not any("kind" in r for r in rows):
        return rows  # already legacy role/content shape

    out: list[dict[str, Any]] = []
    assistant_by_turn: dict[Any, dict[str, Any]] = {}
    warned_version = False
    for r in rows:
        v = r.get("v")
        if v is not None and v != _SUPPORTED_TRAJECTORY_VERSION and not warned_version:
            # Loud signal: the schema drifted. Map best-effort below, but this is
            # exactly the silent-empty-trace failure mode we never want again.
            _logger.warning(
                "trajectory_schema_version_unsupported",
                version=v,
                supported=_SUPPORTED_TRAJECTORY_VERSION,
            )
            warned_version = True

        kind = r.get("kind")
        if kind == "meta":
            out.append({
                "role": "system",
                "content": "",
                "_meta": {"model": r.get("model", ""), "timestamp": r.get("ts")},
            })
        elif kind == "assistant":
            tool_calls = []
            for tc in r.get("tool_calls") or []:
                args = tc.get("arguments", "")
                if not isinstance(args, str):
                    args = json.dumps(args, ensure_ascii=False)
                tool_calls.append({
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {"name": tc.get("name", "tool"), "arguments": args},
                })
            row = {
                "role": "assistant",
                "content": r.get("text", ""),
                "tool_calls": tool_calls,
                "_meta": {},
            }
            out.append(row)
            assistant_by_turn[r.get("turn")] = row
        elif kind == "tool_result":
            out.append({
                "role": "tool",
                "name": r.get("name", "tool"),
                "tool_call_id": r.get("tool_use_id", ""),
                "content": r.get("content", ""),
                "_meta": {"is_error": bool(r.get("is_error"))},
            })
        elif kind == "usage":
            target = assistant_by_turn.get(r.get("turn"))
            if target is not None:
                target["_meta"]["usage"] = {
                    "input_tokens": r.get("input_tokens", 0),
                    "output_tokens": r.get("output_tokens", 0),
                    "cache_read_input_tokens": r.get("cache_read_input_tokens", 0),
                    "cache_creation_input_tokens": r.get("cache_creation_input_tokens", 0),
                }
        elif kind == "end":
            pass  # terminal marker; trace status comes from job.status
        elif kind is not None:
            _logger.warning("trajectory_unknown_kind", kind=kind)
    return out


def _parse_ts(value: str | None) -> datetime:
    """Parse an ISO-8601 timestamp string to a timezone-aware datetime.

    Falls back to the Unix epoch when the value is absent or unparseable,
    so callers always get a valid datetime without a try/except at each site.
    """
    if not value:
        return _EPOCH
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _EPOCH


def _iso(dt: datetime) -> str:
    """Render a datetime as a Langfuse-friendly UTC ISO-8601 string."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_ingestion_batch(
    rows: list[dict[str, Any]], job: JobMeta
) -> list[dict[str, Any]]:
    """Map OHRS trajectory rows + job metadata to Langfuse ingestion events.

    Pure (no I/O). Returns envelope events for POST as ``{"batch": [...]}``.

    Emits three event types:
    - ``trace-create``: one per job, keyed on ``message_id``.
    - ``generation-create``: one per assistant turn with token usage.
    - ``span-create``: one per tool-call result, parented to the issuing
      generation and marked ERROR when the tool returned an error.

    Accepts both the current OHRS kind-tagged trajectory schema and the legacy
    role/content shape; the former is normalized to the latter up front.
    """
    rows = _normalize_rows(rows)

    # --- Pass 0: extract model + t0 from the system message ---
    model = ""
    system_ts: str | None = None
    for r in rows:
        if r.get("role") == "system":
            meta = r.get("_meta") or {}
            model = meta.get("model") or ""
            system_ts = meta.get("timestamp")
            break
    # Trace time: prefer the reliable job/export wall-clock (``job.timestamp``).
    # OHRS writes the trajectory's own system timestamp wrong/future-dated, so
    # it is only a last-resort fallback when the exporter did not stamp a time.
    t0 = _parse_ts(job.timestamp or system_ts)

    # --- Last assistant text becomes the trace-level output ---
    trace_output = job.query
    for r in reversed(rows):
        if r.get("role") == "assistant" and (r.get("content") or "").strip():
            trace_output = r["content"]
            break

    # --- Build a call_map: tool_call_id -> {name, arguments} ---
    call_map: dict[str, dict[str, Any]] = {}
    for r in rows:
        for tc in r.get("tool_calls") or []:
            fn = tc.get("function") or {}
            call_map[tc.get("id", "")] = {
                "name": fn.get("name", "tool"),
                "arguments": fn.get("arguments", ""),
            }

    trace_id = job.message_id
    events: list[dict[str, Any]] = [{
        "id": f"{trace_id}-trace",
        "type": "trace-create",
        "timestamp": _iso(t0),
        "body": {
            "id": trace_id,
            "name": job.mode,
            "userId": job.user_id,
            "sessionId": job.session_id,
            "input": job.query,
            "output": trace_output,
            "tags": [t for t in (job.mode, model, job.status) if t],
            "metadata": {
                "message_id": job.message_id,
                "model": model,
                "status": job.status,
                "block_count": job.block_count,
                "section_count": job.section_count,
            },
            "timestamp": _iso(t0),
        },
    }]

    # --- Pass 1: emit generation-create per assistant turn, span-create per tool result ---
    clock_ms = 0
    gen_index = 0
    current_gen_id: str | None = None

    for r in rows:
        meta = r.get("_meta") or {}
        dur = int(meta.get("_t_ms") or 0)
        start = t0 + timedelta(milliseconds=clock_ms)
        end = start + timedelta(milliseconds=dur)
        clock_ms += max(dur, 1)  # strictly increasing -> stable ordering

        if r.get("role") == "assistant":
            gen_index += 1
            gen_id = f"{trace_id}-gen-{gen_index}"
            current_gen_id = gen_id
            body: dict[str, Any] = {
                "id": gen_id,
                "traceId": trace_id,
                "name": f"turn-{gen_index}",
                "startTime": _iso(start),
                "endTime": _iso(end),
                "model": model,
                "output": {
                    "content": r.get("content", ""),
                    "tool_calls": r.get("tool_calls") or [],
                },
                "level": _LEVEL_DEFAULT,
            }
            usage = meta.get("usage")
            if isinstance(usage, dict):
                body["usageDetails"] = {
                    "input": usage.get("input_tokens", 0),
                    "output": usage.get("output_tokens", 0),
                    "cache_read_input_tokens": usage.get(
                        "cache_read_input_tokens", 0
                    ),
                    "cache_creation_input_tokens": usage.get(
                        "cache_creation_input_tokens", 0
                    ),
                }
            events.append({
                "id": f"{gen_id}-evt",
                "type": "generation-create",
                "timestamp": _iso(start),
                "body": body,
            })

        elif r.get("role") == "tool":
            tcid = r.get("tool_call_id", "")
            call = call_map.get(tcid, {})
            span_id = f"{trace_id}-span-{tcid}" if tcid else f"{trace_id}-span-{clock_ms}"
            is_error = bool(meta.get("is_error"))
            events.append({
                "id": f"{span_id}-evt",
                "type": "span-create",
                "timestamp": _iso(start),
                "body": {
                    "id": span_id,
                    "traceId": trace_id,
                    "parentObservationId": current_gen_id,
                    "name": call.get("name") or r.get("name", "tool"),
                    "startTime": _iso(start),
                    "endTime": _iso(end),
                    "input": call.get("arguments", ""),
                    "output": r.get("content", ""),
                    "level": _LEVEL_ERROR if is_error else _LEVEL_DEFAULT,
                    "statusMessage": "tool error" if is_error else None,
                },
            })

    return events
