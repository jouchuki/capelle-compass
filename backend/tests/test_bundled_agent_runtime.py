"""Verify real bundled workspaces satisfy the backend's execution contract."""

from pathlib import Path

import pytest

from capelle_platform.executor.impl_ohrs import OhrsExecutor
from capelle_platform.executor.modes.impl_groeikern import GroeikernModeConfig
from capelle_platform.executor.modes.impl_jeugdzorg import JeugdzorgModeConfig


@pytest.mark.parametrize("mode_class", [GroeikernModeConfig, JeugdzorgModeConfig])
def test_bundled_workspace_materializes_job_plugin(mode_class, settings_factory, tmp_path):
    root = Path(__file__).resolve().parents[2]
    settings = settings_factory(
        ohrs_working_dir=root / "agent",
        ohrs_working_dir_jeugdzorg=root / "agent/jeugdzorg",
        ohrs_tools_bin=root / ".venv-agent/bin",
    )
    config = mode_class(settings)
    job_dir = tmp_path / config.mode
    job_dir.mkdir()
    OhrsExecutor(settings)._materialize_job_openharness(job_dir, config)
    skills = job_dir / ".openharnessrs/plugins/capelle/skills"
    assert all(path.is_file() for path in config.required_skill_files(skills))
    assert settings.ohrs_tools_bin in config.path_additions()
    if config.mode == "groeikern":
        assert Path(config.extra_env()["GROEIKERNEN_DATA"]) == root / "data/groeikernen"
    else:
        assert all(path.is_file() for path in config.workspace_symlinks().values())
