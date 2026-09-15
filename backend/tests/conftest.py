"""
Shared test fixtures for the Capelle Platform backend test-suite.

The fixtures here are deliberately narrow in scope — each test file
composes what it needs. The most important concern this module solves
is producing a :class:`Settings` instance that does not pick up the
developer's ambient ``OPENAI_API_KEY`` / ``HF_TOKEN`` / ``CAPELLE_*``
env vars. ``pydantic-settings`` with ``extra='forbid'`` will otherwise
fail construction if any unexpected ``*_api_key`` style env var is set.

We scrub those env vars at collection time and provide a
``settings_factory`` fixture that returns fresh :class:`Settings`
instances with overrides applied.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure the backend package is importable regardless of where pytest
# is invoked from.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def _scrub_stray_env() -> None:
    """
    Drop env vars that would make ``Settings()`` refuse to instantiate.

    ``pydantic-settings`` with ``extra='forbid'`` (the default on this
    project) raises when it sees an env var whose name doesn't map to a
    declared field — including things like ``OPENAI_API_KEY`` that live
    in the ambient shell. We scrub a small allowlist rather than
    wholesale-clearing ``os.environ`` so pytest plugins / asyncio's
    zoneinfo lookups still work.
    """
    for key in list(os.environ):
        lowered = key.lower()
        if lowered in {
            "openai_api_key",
            "hf_token",
            "wsgj_api_key",
            "anthropic_api_key",
        }:
            os.environ.pop(key, None)


_scrub_stray_env()


@pytest.fixture
def settings_factory():
    """
    Return a callable that produces a fresh :class:`Settings` instance
    with the given overrides plus a unique temp SQLite path.

    The JWT secret is pre-filled with a >=32-char dev value; callers
    that care about the secret validation can still override it via
    ``**overrides``.
    """
    from capelle_platform.settings import Settings

    created_paths: list[Path] = []

    def _make(**overrides):
        if "sqlite_path" not in overrides:
            tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            tmp.close()
            path = Path(tmp.name)
            created_paths.append(path)
            overrides["sqlite_path"] = path
        overrides.setdefault(
            "jwt_secret", "test-secret-minimum-32-characters-long-ok"
        )
        return Settings(**overrides)

    yield _make

    for p in created_paths:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
