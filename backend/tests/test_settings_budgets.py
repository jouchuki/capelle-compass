"""Per-mode budget defaults — groeikern gets headroom for deep cascades."""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from capelle_platform.settings import Settings

_JWT = "x" * 32


def test_groeikern_budget_has_headroom() -> None:
    s = Settings(jwt_secret=_JWT)
    assert s.ohrs_groeikern_max_turns >= 160
    assert s.ohrs_groeikern_job_timeout_seconds >= 3300


def test_jeugdzorg_budget_unchanged() -> None:
    s = Settings(jwt_secret=_JWT)
    assert s.ohrs_jeugdzorg_max_turns == 20
    assert s.ohrs_jeugdzorg_job_timeout_seconds == 900
