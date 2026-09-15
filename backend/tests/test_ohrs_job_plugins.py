from __future__ import annotations

import json
from pathlib import Path

import pytest

from capelle_platform.executor.impl_ohrs import OhrsExecutor
from capelle_platform.executor.modes import GroeikernModeConfig
from capelle_platform.models.job import JobMessage


def _write_capelle_repo_agent(root: Path) -> None:
    plugin_dir = root / ".openharnessrs" / "plugins" / "capelle"
    skills_dir = root / "skills"
    plugin_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)
    (root / ".openharnessrs" / "settings.json").write_text(
        json.dumps({"enabled_plugins": {}, "provider": "openai-codex"}),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.json").write_text(
        json.dumps({
            "name": "capelle",
            "version": "1.0.0",
            "enabled_by_default": True,
            "skills_dir": "skills",
        }),
        encoding="utf-8",
    )
    # groeikernen.md is required by GroeikernModeConfig (preflight) — the WIP
    # added it to the legacy executor's hardcoded list; the refactor moves
    # the list onto the mode config, but the test fixture still has to mint
    # every file the config's preflight checks for.
    for name in [
        "beleid",
        "budget",
        "bewonersenquete",
        "capelle-analyse",
        "cbs",
        "cube",
        "groeikernen",
        "fan-out",
        "graph",
    ]:
        (skills_dir / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: test {name}\n---\n# {name}\n",
            encoding="utf-8",
        )


def test_job_settings_force_capelle_plugin_enabled(settings_factory, tmp_path: Path) -> None:
    repo_agent = tmp_path / "repo-agent"
    jobs_dir = tmp_path / "jobs"
    _write_capelle_repo_agent(repo_agent)
    settings = settings_factory(ohrs_working_dir=repo_agent, ohrs_jobs_dir=jobs_dir)
    executor = OhrsExecutor(settings)

    job_dir = jobs_dir / "msg-1"
    job_dir.mkdir(parents=True)
    # _write_job_settings now takes the mode's working_dir explicitly so the
    # executor can serve multiple modes from one instance.
    settings_path = executor._write_job_settings(job_dir, "msg-1", repo_agent)

    written = json.loads(settings_path.read_text(encoding="utf-8"))
    assert written["enabled_plugins"]["capelle"] is True


def test_materializes_job_local_capelle_plugin_for_ohrs_cwd_discovery(
    settings_factory,
    tmp_path: Path,
) -> None:
    repo_agent = tmp_path / "repo-agent"
    jobs_dir = tmp_path / "jobs"
    _write_capelle_repo_agent(repo_agent)
    settings = settings_factory(ohrs_working_dir=repo_agent, ohrs_jobs_dir=jobs_dir)
    executor = OhrsExecutor(settings)

    job_dir = jobs_dir / "msg-2"
    job_dir.mkdir(parents=True)
    # _materialize_job_openharness now consumes a ModeConfig (per-mode
    # working_dir + required-skill-files) instead of reaching into
    # executor._working_dir directly.
    executor._materialize_job_openharness(job_dir, GroeikernModeConfig(settings))

    plugin_dir = job_dir / ".openharnessrs" / "plugins" / "capelle"
    assert json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))["name"] == "capelle"
    assert (plugin_dir / "skills" / "beleid.md").exists()
    assert (plugin_dir / "skills" / "budget.md").exists()
    assert (plugin_dir / "skills" / "bewonersenquete.md").exists()
    assert (plugin_dir / "skills" / "capelle-analyse.md").exists()
    assert (plugin_dir / "skills" / "cbs.md").exists()
    assert (plugin_dir / "skills" / "groeikernen.md").exists()


@pytest.mark.asyncio
async def test_fresh_ohrs_job_returns_conversational_result_when_analysis_json_missing(
    settings_factory,
    tmp_path: Path,
) -> None:
    """
    A first-turn job that exits 0 but never writes analysis.json must no
    longer raise RuntimeError.  The executor should fall through to the
    conversational-response path and return a ``chat_response`` or
    ``ohrs_response`` result dict instead of failing the job.
    """
    repo_agent = tmp_path / "repo-agent"
    jobs_dir = tmp_path / "jobs"
    _write_capelle_repo_agent(repo_agent)

    # Print-mode JSON object per contract C2: a single object with v/result/
    # model/status/error/usage. The executor parses `result` for the final
    # assistant text.
    fake_ohrs = tmp_path / "fake-ohrs"
    fake_ohrs.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({\n"
        "    'v': 1,\n"
        "    'result': 'done but no file',\n"
        "    'model': 'test-model',\n"
        "    'status': 'ok',\n"
        "    'error': None,\n"
        "    'usage': {\n"
        "        'input_tokens': 10, 'output_tokens': 5,\n"
        "        'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0,\n"
        "    },\n"
        "}))\n",
        encoding="utf-8",
    )
    fake_ohrs.chmod(0o755)

    settings = settings_factory(
        ohrs_binary=str(fake_ohrs),
        ohrs_working_dir=repo_agent,
        ohrs_jobs_dir=jobs_dir,
        job_timeout_seconds=70,
        # Keep below job_timeout_seconds − 60 so the EXEC-4 model_validator
        # (elicitation must finish before the job timeout) is satisfied.
        elicitation_timeout_seconds=5,
    )
    executor = OhrsExecutor(settings)
    job = JobMessage(
        session_id="session-1",
        message_id="msg-missing-analysis",
        user_id="user-1",
        query="Wat zijn de IT expenses in Capelle?",
        skill="capelle-analyse",
        trace_id="trace-1",
        history=[],
    )

    # Must NOT raise; must return a conversational result dict.
    result = await executor.execute(job)
    assert result.get("type") in {"chat_response", "ohrs_response"}, (
        f"Expected conversational result type, got: {result.get('type')!r}"
    )
    assert "content" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected_max_turns", [
    ("groeikern", "160"),
    ("jeugdzorg", "20"),
])
async def test_execute_uses_per_mode_max_turns_in_argv(
    settings_factory, tmp_path: Path, mode: str, expected_max_turns: str
) -> None:
    """execute() must pass the mode-specific --max-turns to the ohrs binary."""
    repo_agent = tmp_path / "repo-agent"
    jobs_dir = tmp_path / "jobs"
    _write_capelle_repo_agent(repo_agent)
    # jeugdzorg mode needs capelle-jeugdzorg.md in the skills dir
    (repo_agent / "skills" / "capelle-jeugdzorg.md").write_text(
        "---\nname: capelle-jeugdzorg\ndescription: test\n---\n",
        encoding="utf-8",
    )

    # A fake binary that echoes its own argv to a file, then outputs valid JSON.
    argv_log = tmp_path / "argv.json"
    fake_ohrs = tmp_path / "fake-ohrs"
    fake_ohrs.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(argv_log)!r}, 'w').write(json.dumps(sys.argv))\n"
        "print(json.dumps({"
        "'v': 1, 'result': 'done', 'model': 'test', "
        "'status': 'ok', 'error': None, "
        "'usage': {'input_tokens': 1, 'output_tokens': 1, "
        "'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}}"
        "))\n",
        encoding="utf-8",
    )
    fake_ohrs.chmod(0o755)

    settings = settings_factory(
        ohrs_binary=str(fake_ohrs),
        ohrs_working_dir=repo_agent,
        ohrs_working_dir_jeugdzorg=repo_agent,  # reuse for jeugdzorg in test
        ohrs_jobs_dir=jobs_dir,
        job_timeout_seconds=70,
        elicitation_timeout_seconds=5,
    )
    executor = OhrsExecutor(settings)
    job = JobMessage(
        session_id="session-1",
        message_id=f"msg-mode-{mode}",
        user_id="user-1",
        query="test",
        skill="capelle-analyse",
        trace_id="trace-1",
        history=[],
        mode=mode,  # type: ignore[arg-type]
    )

    await executor.execute(job)

    argv = json.loads(argv_log.read_text(encoding="utf-8"))
    # Find --max-turns in the argv
    assert "--max-turns" in argv, f"--max-turns not found in argv: {argv}"
    idx = argv.index("--max-turns")
    assert argv[idx + 1] == expected_max_turns, (
        f"Expected --max-turns {expected_max_turns} for mode={mode!r}, "
        f"got {argv[idx + 1]!r}"
    )


def test_groeikern_uses_per_mode_max_turns_and_runtimemaxsec(monkeypatch):
    """_wrap_in_systemd_run must accept timeout_seconds kwarg and emit RuntimeMaxSec."""
    from capelle_platform.executor.impl_ohrs import OhrsExecutor
    from capelle_platform.settings import Settings
    s = Settings(jwt_secret="x" * 32)  # defaults: groeikern 3300s / 160 turns
    ex = OhrsExecutor(settings=s, progress_callback=lambda *a, **k: None)
    wrapped = ex._wrap_in_systemd_run(
        ["ohrs", "--max-turns", "160"], __import__("pathlib").Path("/tmp/j"),
        __import__("pathlib").Path("/tmp/j/settings.json"), {}, "msg1",
        timeout_seconds=3300,
    )
    if wrapped is not None:  # only on hosts with the capelle-agent user
        assert any(p == "--property=RuntimeMaxSec=3335" for p in wrapped)
