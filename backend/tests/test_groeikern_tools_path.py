"""
Regression: the groeikern agent must be able to resolve the ``capelle-ask``
CLI (and the other ``capelle-*`` / ``cbs`` tools) on its ``PATH``.

In production the CLI venv was relocated from ``<working_dir>/venv/bin`` to a
shared ``/opt/tools-venv/bin`` (2026-06-02), and ``<working_dir>/venv`` was
deleted. ``GroeikernModeConfig.path_additions()`` only declared the now-dead
``<working_dir>/venv/bin``, so the directory holding the real binaries was
never prepended to the agent's PATH. The agent's ``capelle-ask`` invocation
then failed with ``exit 127 / capelle-ask: command not found`` — the
elicitation never reached ``/internal/ask``, no card rendered, and the UI was
left showing only the truncated ``Uitvoeren: capelle-ask ...`` Bash step.

These tests pin that ``path_additions()`` declares the real tools bin dir so
the agent PATH is correct *by construction*, not by accident of an inherited
process PATH.
"""

from __future__ import annotations

from pathlib import Path

from capelle_platform.executor.modes.factory import ModeConfigFactory
from capelle_platform.settings import Settings

_JWT = "x" * 32


def _clean(monkeypatch) -> None:
    for k in list(__import__("os").environ):
        if k.startswith("CAPELLE_"):
            monkeypatch.delenv(k, raising=False)


def test_groeikern_path_additions_include_tools_bin(monkeypatch) -> None:
    _clean(monkeypatch)
    s = Settings(jwt_secret=_JWT)
    cfg = ModeConfigFactory.from_mode("groeikern", s)
    additions = cfg.path_additions()
    # The directory holding the real capelle-ask / cbs / capelle-* binaries
    # MUST be among the PATH additions, or the agent cannot run them.
    assert s.ohrs_tools_bin in additions, (
        f"tools bin {s.ohrs_tools_bin} not in PATH additions {additions}"
    )


def test_ohrs_tools_bin_defaults_to_prod_location(monkeypatch) -> None:
    _clean(monkeypatch)
    s = Settings(jwt_secret=_JWT)
    assert s.ohrs_tools_bin == Path("/opt/compass/tools/bin")


def test_ohrs_tools_bin_overridable_via_env(monkeypatch) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("CAPELLE_OHRS_TOOLS_BIN", "/custom/venv/bin")
    s = Settings(jwt_secret=_JWT)
    assert s.ohrs_tools_bin == Path("/custom/venv/bin")
    cfg = ModeConfigFactory.from_mode("groeikern", s)
    assert Path("/custom/venv/bin") in cfg.path_additions()
