from capelle_platform.observability.mapper import build_ingestion_batch
from capelle_platform.observability.types import JobMeta

JOB = JobMeta(
    message_id="job123",
    mode="groeikern",
    user_id="s.roshani@example.nl",
    session_id="sess1",
    query="Vergelijk de groeikernen.",
    status="completed",
    block_count=12,
    section_count=0,
)

ROWS = [
    {"role": "system", "content": "...",
     "_meta": {"model": "gpt-5.4", "timestamp": "2026-06-11T09:32:31Z", "tools": []}},
    {"role": "user", "content": "Vergelijk de groeikernen."},
    {"role": "assistant", "content": "Plan...",
     "tool_calls": [{"id": "call_1", "type": "function",
                     "function": {"name": "Bash", "arguments": "{\"command\":\"ls\"}"}}],
     "_meta": {"_t_ms": 0, "usage": {"input_tokens": 8400, "output_tokens": 85,
                                     "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}},
    {"role": "tool", "name": "Bash", "tool_call_id": "call_1", "content": "file1\nfile2",
     "_meta": {"_t_ms": 0, "is_error": False}},
    {"role": "assistant", "content": "Klaar.",
     "_meta": {"_t_ms": 0, "usage": {"input_tokens": 8498, "output_tokens": 370,
                                     "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}},
]


def test_emits_single_trace_create_with_job_meta():
    events = build_ingestion_batch(ROWS, JOB)
    traces = [e for e in events if e["type"] == "trace-create"]
    assert len(traces) == 1
    body = traces[0]["body"]
    assert body["id"] == "job123"
    assert body["name"] == "groeikern"
    assert body["userId"] == "s.roshani@example.nl"
    assert body["sessionId"] == "sess1"
    assert body["input"] == "Vergelijk de groeikernen."
    assert body["output"] == "Klaar."          # last assistant text
    assert "gpt-5.4" in body["tags"]
    assert body["metadata"]["model"] == "gpt-5.4"
    assert body["metadata"]["block_count"] == 12


def test_each_assistant_turn_becomes_a_generation_with_tokens():
    events = build_ingestion_batch(ROWS, JOB)
    gens = [e for e in events if e["type"] == "generation-create"]
    assert len(gens) == 2                                  # two assistant turns
    first = gens[0]["body"]
    assert first["traceId"] == "job123"
    assert first["model"] == "gpt-5.4"
    assert first["usageDetails"]["input"] == 8400
    assert first["usageDetails"]["output"] == 85
    assert first["level"] == "DEFAULT"
    # monotonically increasing start times (stable ordering even at _t_ms=0)
    starts = [g["body"]["startTime"] for g in gens]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)


ERR_ROWS = ROWS + [
    {"role": "assistant", "content": "",
     "tool_calls": [{"id": "call_2", "type": "function",
                     "function": {"name": "Write", "arguments": "{\"path\":\"/bad\"}"}}],
     "_meta": {"_t_ms": 5, "usage": {"input_tokens": 10, "output_tokens": 5}}},
    {"role": "tool", "name": "Write", "tool_call_id": "call_2", "content": "permission denied",
     "_meta": {"_t_ms": 2, "is_error": True}},
]


def test_tool_calls_become_spans_parented_to_their_generation():
    events = build_ingestion_batch(ROWS, JOB)
    spans = [e for e in events if e["type"] == "span-create"]
    assert len(spans) == 1                                 # one tool result
    b = spans[0]["body"]
    assert b["name"] == "Bash"
    assert b["traceId"] == "job123"
    assert b["parentObservationId"] == "job123-gen-1"      # the turn that issued it
    assert b["input"] == "{\"command\":\"ls\"}"
    assert b["output"] == "file1\nfile2"
    assert b["level"] == "DEFAULT"


def test_error_tool_results_get_error_level():
    events = build_ingestion_batch(ERR_ROWS, JOB)
    spans = [e for e in events if e["type"] == "span-create"]
    err = [s for s in spans if s["body"]["name"] == "Write"][0]["body"]
    assert err["level"] == "ERROR"
    assert err["statusMessage"]


def test_trace_dated_by_job_timestamp_overrides_trajectory():
    """The reliable export wall-clock (job.timestamp) must win over the
    trajectory's own (OHRS-written, often future-dated) system timestamp."""
    from dataclasses import replace

    job = replace(JOB, timestamp="2026-06-02T11:00:00+00:00")
    events = build_ingestion_batch(ROWS, job)
    trace = [e for e in events if e["type"] == "trace-create"][0]
    # ROWS' system timestamp is 2026-06-11 (bogus/future); job.timestamp wins.
    assert trace["body"]["timestamp"].startswith("2026-06-02T11:00:00")


def test_trace_falls_back_to_trajectory_ts_when_job_timestamp_absent():
    """Back-compat: with no job.timestamp, the trajectory system ts is used."""
    events = build_ingestion_batch(ROWS, JOB)  # JOB has timestamp=None
    trace = [e for e in events if e["type"] == "trace-create"][0]
    assert trace["body"]["timestamp"].startswith("2026-06-11")


# --- New OHRS trajectory schema (kind-tagged, v1) ----------------------------
# OHRS migrated trajectory.jsonl from {role,content,_meta} to a kind-discriminated
# event schema (oh crates/oh-harness/src/trajectory.rs). The mapper normalizes
# these back to the legacy role/content shape it understands. These rows mirror
# ROWS above so the same trace/generation/span outcomes must hold.
NEW_ROWS = [
    {"v": 1, "kind": "meta", "ts": "2026-06-11T09:32:31Z",
     "model": "gpt-5.4", "session_id": "sess1"},
    {"v": 1, "kind": "assistant", "turn": 1, "seq": 1, "text": "Plan...",
     "tool_calls": [{"id": "call_1", "name": "Bash", "arguments": {"command": "ls"}}]},
    {"v": 1, "kind": "tool_result", "turn": 1, "seq": 2, "tool_use_id": "call_1",
     "name": "Bash", "content": "file1\nfile2", "is_error": False},
    {"v": 1, "kind": "usage", "turn": 1, "input_tokens": 8400, "output_tokens": 85,
     "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
    {"v": 1, "kind": "assistant", "turn": 2, "seq": 3, "text": "Klaar.", "tool_calls": []},
    {"v": 1, "kind": "usage", "turn": 2, "input_tokens": 8498, "output_tokens": 370,
     "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
    {"v": 1, "kind": "end", "turn": 2, "status": "ok", "error": None},
]


def test_new_schema_trace_output_is_last_assistant_text():
    events = build_ingestion_batch(NEW_ROWS, JOB)
    trace = [e for e in events if e["type"] == "trace-create"][0]["body"]
    assert trace["output"] == "Klaar."        # NOT the query echo (the bug)
    assert trace["metadata"]["model"] == "gpt-5.4"
    assert "gpt-5.4" in trace["tags"]


def test_new_schema_assistant_turns_become_generations_with_tokens():
    events = build_ingestion_batch(NEW_ROWS, JOB)
    gens = [e for e in events if e["type"] == "generation-create"]
    assert len(gens) == 2                       # was 0 before the shim
    first = gens[0]["body"]
    assert first["model"] == "gpt-5.4"
    assert first["usageDetails"]["input"] == 8400
    assert first["usageDetails"]["output"] == 85


def test_new_schema_tool_results_become_spans():
    events = build_ingestion_batch(NEW_ROWS, JOB)
    spans = [e for e in events if e["type"] == "span-create"]
    assert len(spans) == 1                       # was 0 before the shim
    b = spans[0]["body"]
    assert b["name"] == "Bash"
    assert b["parentObservationId"] == "job123-gen-1"
    assert b["input"] == "{\"command\": \"ls\"}"   # object arguments stringified
    assert b["output"] == "file1\nfile2"
    assert b["level"] == "DEFAULT"


def test_new_schema_error_tool_result_gets_error_level():
    rows = NEW_ROWS[:-1] + [
        {"v": 1, "kind": "assistant", "turn": 3, "seq": 4, "text": "",
         "tool_calls": [{"id": "call_2", "name": "Write", "arguments": {"path": "/bad"}}]},
        {"v": 1, "kind": "tool_result", "turn": 3, "seq": 5, "tool_use_id": "call_2",
         "name": "Write", "content": "permission denied", "is_error": True},
        {"v": 1, "kind": "end", "turn": 3, "status": "ok", "error": None},
    ]
    events = build_ingestion_batch(rows, JOB)
    err = [s for s in events if s["type"] == "span-create"
           and s["body"]["name"] == "Write"][0]["body"]
    assert err["level"] == "ERROR"
    assert err["statusMessage"]


def test_legacy_rows_still_work_unchanged():
    """The shim must be a no-op for the old role/content schema."""
    assert build_ingestion_batch(ROWS, JOB) == build_ingestion_batch(ROWS, JOB)
    gens = [e for e in build_ingestion_batch(ROWS, JOB)
            if e["type"] == "generation-create"]
    assert len(gens) == 2
