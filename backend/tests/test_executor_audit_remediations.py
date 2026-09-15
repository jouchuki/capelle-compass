"""
Tests for the executor audit remediations (EXEC-1, 2, 3, 4, 5, 7, 8).

These lock the capelle↔ohrs interface contract:
- C1: the versioned, ``kind``-tagged streaming trajectory JSONL that
  :meth:`OhrsExecutor._scan_trajectory` and
  :meth:`OhrsExecutor._parse_trajectory_line` consume.
- C2: the single print-mode JSON object on stdout that
  :meth:`OhrsExecutor._parse_print_mode_json` consumes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from capelle_platform.executor.impl_cli import CLIExecutor
from capelle_platform.executor.impl_ohrs import (
    OhrsExecutor,
    _JOB_UNIT_PREFIX,
    _JOB_UNIT_SUFFIX,
    _TRAJECTORY_BIG_TOOL_RESULT_BYTES,
)
from capelle_platform.settings import (
    ELICITATION_JOB_TIMEOUT_MARGIN_SECONDS,
    Settings,
)


# ---------------------------------------------------------------------------
# C1 trajectory helpers
# ---------------------------------------------------------------------------

def _line(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _write_trajectory(path: Path, objs: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(_line(o) for o in objs) + "\n", encoding="utf-8")


def _meta(model: str = "claude-test") -> dict[str, Any]:
    return {"v": 1, "kind": "meta", "ts": "2026-05-27T00:00:00Z",
            "model": model, "session_id": "sess-1"}


def _usage(turn: int, **k: int) -> dict[str, Any]:
    base = {"v": 1, "kind": "usage", "turn": turn, "input_tokens": 0,
            "output_tokens": 0, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0}
    base.update(k)
    return base


def _assistant(turn: int, seq: int, text: str = "",
               tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"v": 1, "kind": "assistant", "turn": turn, "seq": seq,
            "text": text, "tool_calls": tool_calls or []}


def _tool_result(turn: int, seq: int, tool_use_id: str, name: str,
                 content: str, is_error: bool = False) -> dict[str, Any]:
    return {"v": 1, "kind": "tool_result", "turn": turn, "seq": seq,
            "tool_use_id": tool_use_id, "name": name, "content": content,
            "is_error": is_error}


def _end(turn: int, status: str = "ok",
         error: str | None = None) -> dict[str, Any]:
    return {"v": 1, "kind": "end", "turn": turn, "status": status,
            "error": error}


# ---------------------------------------------------------------------------
# EXEC-1 / EXEC-7: combined scan — usage aggregation
# ---------------------------------------------------------------------------

class TestScanUsage:
    def test_sums_usage_lines(self, tmp_path: Path) -> None:
        traj = tmp_path / "trajectory.jsonl"
        _write_trajectory(traj, [
            _meta("claude-x"),
            _assistant(1, 1, tool_calls=[
                {"id": "t1", "name": "Bash", "arguments": {"command": "cbs get x"}}]),
            _usage(1, input_tokens=100, output_tokens=20,
                   cache_read_input_tokens=5, cache_creation_input_tokens=2),
            _tool_result(1, 2, "t1", "Bash", "ok"),
            _assistant(2, 3, text="Klaar."),
            _usage(2, input_tokens=300, output_tokens=40,
                   cache_read_input_tokens=10, cache_creation_input_tokens=3),
            _end(2, "ok"),
        ])
        usage = OhrsExecutor._aggregate_usage_from_trajectory(traj)
        assert usage is not None
        assert usage["model"] == "claude-x"
        assert usage["turns"] == 2
        assert usage["input_tokens"] == 400
        assert usage["output_tokens"] == 60
        assert usage["cache_read_input_tokens"] == 15
        assert usage["cache_creation_input_tokens"] == 5
        assert usage["total_tokens"] == 400 + 60 + 15 + 5

    def test_no_usage_lines_returns_none(self, tmp_path: Path) -> None:
        traj = tmp_path / "trajectory.jsonl"
        _write_trajectory(traj, [_meta(), _assistant(1, 1, text="hi")])
        assert OhrsExecutor._aggregate_usage_from_trajectory(traj) is None

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert OhrsExecutor._aggregate_usage_from_trajectory(
            tmp_path / "nope.jsonl") is None

    def test_legacy_meta_usage_schema_ignored(self, tmp_path: Path) -> None:
        """Old OpenAI-style `_meta.usage` lines must NOT be counted (C1)."""
        traj = tmp_path / "trajectory.jsonl"
        traj.write_text(
            _line({"role": "assistant", "content": "x",
                   "_meta": {"usage": {"input_tokens": 999}}}) + "\n",
            encoding="utf-8",
        )
        assert OhrsExecutor._aggregate_usage_from_trajectory(traj) is None


# ---------------------------------------------------------------------------
# EXEC-1: best-text recovery
# ---------------------------------------------------------------------------

class TestBestText:
    def test_joins_assistant_texts(self, tmp_path: Path) -> None:
        traj = tmp_path / "trajectory.jsonl"
        _write_trajectory(traj, [
            _meta(),
            _assistant(1, 1, text="Eerste."),
            _assistant(2, 2, text="Tweede."),
        ])
        ex = OhrsExecutor(_settings(tmp_path))
        assert ex._extract_best_text(None, traj) == "Eerste.\n\nTweede."

    def test_plain_text_stdout_wins(self, tmp_path: Path) -> None:
        ex = OhrsExecutor(_settings(tmp_path))
        out = ex._extract_best_text(b"plain prose answer", tmp_path / "x.jsonl")
        assert out == "plain prose answer"


# ---------------------------------------------------------------------------
# EXEC-1 / EXEC-7: diagnosis of oversized tool result + empty final turn
# ---------------------------------------------------------------------------

class TestDiagnose:
    def test_big_tool_result_and_empty_final_turn(self, tmp_path: Path) -> None:
        traj = tmp_path / "trajectory.jsonl"
        big = "x" * (_TRAJECTORY_BIG_TOOL_RESULT_BYTES + 10)
        _write_trajectory(traj, [
            _meta(),
            _assistant(1, 1, tool_calls=[
                {"id": "t1", "name": "Bash",
                 "arguments": {"command": "cbs get 84710NED --series"}}]),
            _tool_result(1, 2, "t1", "Bash", big),
            _assistant(2, 3, text="", tool_calls=[]),  # empty final turn
        ])
        msg = OhrsExecutor._diagnose_empty_final_turn(traj)
        assert msg is not None
        assert "voortijdig afgebroken" in msg
        assert "cbs" in msg

    def test_no_diagnosis_when_final_turn_has_text(self, tmp_path: Path) -> None:
        traj = tmp_path / "trajectory.jsonl"
        big = "x" * (_TRAJECTORY_BIG_TOOL_RESULT_BYTES + 10)
        _write_trajectory(traj, [
            _meta(),
            _assistant(1, 1, tool_calls=[
                {"id": "t1", "name": "Bash", "arguments": {"command": "cbs get x"}}]),
            _tool_result(1, 2, "t1", "Bash", big),
            _assistant(2, 3, text="Hier is het rapport."),
        ])
        assert OhrsExecutor._diagnose_empty_final_turn(traj) is None

    def test_no_diagnosis_without_big_result(self, tmp_path: Path) -> None:
        traj = tmp_path / "trajectory.jsonl"
        _write_trajectory(traj, [
            _meta(),
            _assistant(1, 1, text="", tool_calls=[]),  # empty, but no big result
        ])
        assert OhrsExecutor._diagnose_empty_final_turn(traj) is None


# ---------------------------------------------------------------------------
# EXEC-1: live tailer per-line parse
# ---------------------------------------------------------------------------

class TestParseTrajectoryLine:
    def test_assistant_tool_call_yields_running_event(self) -> None:
        line = _line(_assistant(1, 1, tool_calls=[
            {"id": "t1", "name": "Bash", "arguments": {"command": "cbs get x"}}]))
        parsed = OhrsExecutor._parse_trajectory_line(line)
        assert parsed is not None
        event, dedup = parsed
        assert event["status"] == "running"
        assert event["tool"] == "Bash"
        assert dedup == OhrsExecutor.make_dedup_key("Bash", {"command": "cbs get x"})

    def test_tool_result_yields_done_event(self) -> None:
        line = _line(_tool_result(1, 2, "t1", "Bash", "ok"))
        parsed = OhrsExecutor._parse_trajectory_line(line)
        assert parsed is not None
        event, dedup = parsed
        assert event["status"] == "done"
        assert dedup == "result|t1"

    def test_meta_usage_end_are_not_events(self) -> None:
        for obj in (_meta(), _usage(1), _end(1)):
            assert OhrsExecutor._parse_trajectory_line(_line(obj)) is None

    def test_text_only_assistant_is_not_an_event(self) -> None:
        assert OhrsExecutor._parse_trajectory_line(
            _line(_assistant(1, 1, text="hello"))) is None

    def test_wrong_version_ignored(self) -> None:
        assert OhrsExecutor._parse_trajectory_line(
            _line({"v": 99, "kind": "assistant", "tool_calls": []})) is None

    def test_malformed_json_ignored(self) -> None:
        assert OhrsExecutor._parse_trajectory_line("{not json") is None


# ---------------------------------------------------------------------------
# EXEC-2: print-mode JSON (contract C2)
# ---------------------------------------------------------------------------

class TestPrintModeJson:
    def test_parses_c2_object(self) -> None:
        obj = {"v": 1, "result": "Het antwoord.", "model": "m",
               "status": "ok", "error": None,
               "usage": {"input_tokens": 10, "output_tokens": 2,
                         "cache_read_input_tokens": 0,
                         "cache_creation_input_tokens": 0}}
        parsed = OhrsExecutor._parse_print_mode_json(_line(obj), "msg-1")
        assert parsed is not None
        assert parsed["result"] == "Het antwoord."

    def test_parse_ohrs_output_uses_result(self, tmp_path: Path) -> None:
        from capelle_platform.executor.impl_ohrs import _TrajectoryScan
        from capelle_platform.models.job import JobMessage

        ex = OhrsExecutor(_settings(tmp_path))
        job = JobMessage(session_id="s", message_id="m", user_id="u",
                         query="q", skill="capelle-analyse", trace_id="t")
        obj = {"v": 1, "result": "Final text.", "model": "m",
               "status": "ok", "error": None, "usage": {}}
        out = ex._parse_ohrs_output(
            _line(obj).encode(), _TrajectoryScan(), job)
        assert out["content"] == "Final text."
        assert out["type"] == "ohrs_response"

    def test_wrong_version_rejected(self) -> None:
        obj = {"v": 7, "result": "x", "status": "ok"}
        assert OhrsExecutor._parse_print_mode_json(_line(obj), "msg-1") is None

    def test_empty_stdout_returns_none(self) -> None:
        assert OhrsExecutor._parse_print_mode_json("", "msg-1") is None


# ---------------------------------------------------------------------------
# EXEC-3: named transient unit
# ---------------------------------------------------------------------------

class TestJobUnitName:
    def test_unit_name_shape(self) -> None:
        name = OhrsExecutor._job_unit_name("abc123")
        assert name == f"{_JOB_UNIT_PREFIX}abc123{_JOB_UNIT_SUFFIX}"

    def test_unit_name_sanitises_separators(self) -> None:
        name = OhrsExecutor._job_unit_name("a/b c..d")
        assert "/" not in name
        assert " " not in name
        assert name.startswith(_JOB_UNIT_PREFIX)
        assert name.endswith(_JOB_UNIT_SUFFIX)


# ---------------------------------------------------------------------------
# EXEC-4: elicitation/job timeout model_validator
# ---------------------------------------------------------------------------

class TestElicitationTimeoutValidator:
    def test_rejects_elicitation_too_close_to_job_timeout(self) -> None:
        with pytest.raises(ValueError, match="elicitation"):
            Settings(jwt_secret="x" * 32, job_timeout_seconds=100,
                     elicitation_timeout_seconds=100)

    def test_accepts_within_margin(self) -> None:
        s = Settings(jwt_secret="x" * 32, job_timeout_seconds=600,
                     elicitation_timeout_seconds=540)
        assert s.elicitation_timeout_seconds == 540

    def test_boundary_exactly_at_margin_ok(self) -> None:
        jt = 600
        s = Settings(jwt_secret="x" * 32, job_timeout_seconds=jt,
                     elicitation_timeout_seconds=jt -
                     ELICITATION_JOB_TIMEOUT_MARGIN_SECONDS)
        assert s is not None


# ---------------------------------------------------------------------------
# EXEC-5: CLIExecutor composite cancel keys
# ---------------------------------------------------------------------------

class _FakeProc:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True


@pytest.mark.asyncio
class TestCLIExecutorCancel:
    async def test_cancel_terminates_all_tools_for_analysis(
        self, tmp_path: Path) -> None:
        ex = CLIExecutor(_settings(tmp_path))
        p1, p2 = _FakeProc(), _FakeProc()
        ex._register_proc("A", "A:cbs", p1)  # type: ignore[arg-type]
        ex._register_proc("A", "A:beleid", p2)  # type: ignore[arg-type]
        await ex.cancel("A")
        assert p1.terminated and p2.terminated
        assert ex._running_procs == {}
        assert ex._tool_keys == {}

    async def test_cancel_unknown_analysis_is_noop(self, tmp_path: Path) -> None:
        ex = CLIExecutor(_settings(tmp_path))
        await ex.cancel("does-not-exist")  # must not raise

    async def test_cancel_only_targets_its_own_analysis(
        self, tmp_path: Path) -> None:
        ex = CLIExecutor(_settings(tmp_path))
        a, b = _FakeProc(), _FakeProc()
        ex._register_proc("A", "A:cbs", a)  # type: ignore[arg-type]
        ex._register_proc("B", "B:cbs", b)  # type: ignore[arg-type]
        await ex.cancel("A")
        assert a.terminated and not b.terminated
        assert "B:cbs" in ex._running_procs


# ---------------------------------------------------------------------------
# EXEC-8: --bare + --output-format json in spawned cmd
# ---------------------------------------------------------------------------

class TestSpawnFlags:
    def test_bare_and_json_flags_present(self) -> None:
        import inspect
        src = inspect.getsource(OhrsExecutor.execute)
        assert '"--bare"' in src
        assert '"--output-format", "json"' in src


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _settings(tmp_path: Path) -> Settings:
    return Settings(
        jwt_secret="test-secret-minimum-32-characters-long-ok",
        sqlite_path=tmp_path / "t.db",
        ohrs_working_dir=tmp_path / "repo",
        ohrs_jobs_dir=tmp_path / "jobs",
    )
