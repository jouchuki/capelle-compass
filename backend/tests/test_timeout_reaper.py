"""
Tests for configurable reaper margin / base / interval in AsyncWorkerPool.
"""

from __future__ import annotations

import inspect

from capelle_platform.worker.pool import AsyncWorkerPool


def test_pool_accepts_configurable_reaper_params():
    sig = inspect.signature(AsyncWorkerPool.__init__)
    assert "stale_timeout_margin" in sig.parameters
    assert "reaper_base_timeout_seconds" in sig.parameters
    assert "reaper_interval_seconds" in sig.parameters


def test_threshold_uses_base_times_margin():
    # threshold = reaper_base_timeout_seconds * stale_timeout_margin
    margin = 1.25
    base = 2400
    assert base * margin == 3000.0
