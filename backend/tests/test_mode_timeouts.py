"""
Tests for per-mode job/turns/elicitation settings and reaper margin.
"""

from __future__ import annotations

import pytest

from capelle_platform.settings import Settings


_JWT = "x" * 32  # >= 32 chars, satisfies _reject_insecure_jwt_secret


def _clean(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("CAPELLE_"):
            monkeypatch.delenv(k, raising=False)


def test_per_mode_timeout_defaults(monkeypatch):
    _clean(monkeypatch)
    s = Settings(jwt_secret=_JWT)
    assert s.ohrs_groeikern_job_timeout_seconds == 3300
    assert s.ohrs_groeikern_max_turns == 160
    assert s.ohrs_groeikern_elicitation_timeout_seconds == 600
    assert s.ohrs_jeugdzorg_job_timeout_seconds == 900
    assert s.ohrs_jeugdzorg_max_turns == 20
    assert s.ohrs_jeugdzorg_elicitation_timeout_seconds == 300
    assert s.reaper_stale_timeout_margin == 1.25


def test_derived_reaper_threshold(monkeypatch):
    _clean(monkeypatch)
    s = Settings(jwt_secret=_JWT)
    assert s.max_job_timeout_seconds == 3300          # max(600, 3300, 900)
    assert s.reaper_stale_threshold_seconds == 4125.0  # 3300 * 1.25


def test_validator_rejects_margin_not_above_one(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("CAPELLE_REAPER_STALE_TIMEOUT_MARGIN", "1.0")
    with pytest.raises(ValueError, match="reaper_stale_timeout_margin"):
        Settings(jwt_secret=_JWT)


def test_validator_rejects_per_mode_elicitation_too_close(monkeypatch):
    _clean(monkeypatch)
    # Must exceed ceiling = job_timeout(3300) − margin(60) = 3240
    monkeypatch.setenv("CAPELLE_OHRS_GROEIKERN_ELICITATION_TIMEOUT_SECONDS", "3300")
    with pytest.raises(ValueError, match="groeikern"):
        Settings(jwt_secret=_JWT)


from capelle_platform.executor.modes.impl_groeikern import GroeikernModeConfig
from capelle_platform.executor.modes.impl_jeugdzorg import JeugdzorgModeConfig


def test_mode_configs_expose_per_mode_budgets(monkeypatch):
    _clean(monkeypatch)
    s = Settings(jwt_secret=_JWT)
    g = GroeikernModeConfig(s)
    assert g.job_timeout_seconds == 3300
    assert g.max_turns == 160
    assert g.elicitation_timeout_seconds == 600
    j = JeugdzorgModeConfig(s)
    assert j.job_timeout_seconds == 900
    assert j.max_turns == 20
    assert j.elicitation_timeout_seconds == 300
