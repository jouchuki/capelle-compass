"""
Tests for the v2 schema tolerances and the "no report on first turn" recovery path
added to :class:`OhrsExecutor`.

Coverage:
- ``_strip_fabricated_source_urls`` on v2 dicts with ``citations``
- ``_strip_empty_source_rows`` on v2 dicts with ``blocks``
- ``CAPELLE_MESSAGE_ID`` membership in ``_AGENT_ENV_ALLOW``
- Neither post-processor raises on a dict that has neither ``sections``
  nor the v2 keys.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from capelle_platform.executor.impl_ohrs import OhrsExecutor, _BELEID_URL_RE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_beleid_url() -> str:
    """Return a URL that satisfies ``_BELEID_URL_RE``."""
    return (
        "https://capelleaandenijssel.begrotingsapp.nl/"
        "begroting-2024/programma/sociaal-domein"
    )


def _make_v2_analysis(**extra: Any) -> dict[str, Any]:
    """Minimal valid v2 AnalysisResult dict."""
    return {
        "schema_version": 2,
        "id": "abc123",
        "timestamp": "2024-01-01T00:00:00Z",
        "query": "test query",
        "summary": "Test samenvatting.",
        **extra,
    }


# ---------------------------------------------------------------------------
# _strip_fabricated_source_urls — v2 citations
# ---------------------------------------------------------------------------

class TestStripFabricatedSourceUrlsCitations:
    """Unit tests for the v2 citations branch of ``_strip_fabricated_source_urls``."""

    def test_non_beleid_source_url_removed(self) -> None:
        """A citation with source != 'beleid' must have its source_url deleted."""
        analysis = _make_v2_analysis(citations=[
            {"source": "cbs", "source_url": "https://opendata.cbs.nl/made/up"},
        ])
        stripped = OhrsExecutor._strip_fabricated_source_urls(analysis)
        assert stripped == 1
        assert "source_url" not in analysis["citations"][0]

    def test_beleid_valid_url_kept(self) -> None:
        """A beleid citation with a URL that matches ``_BELEID_URL_RE`` must be kept."""
        url = _valid_beleid_url()
        # Confirm our fixture URL actually satisfies the regex.
        assert _BELEID_URL_RE.match(url), f"Fixture URL does not match regex: {url}"
        analysis = _make_v2_analysis(citations=[
            {"source": "beleid", "source_url": url},
        ])
        stripped = OhrsExecutor._strip_fabricated_source_urls(analysis)
        assert stripped == 0
        assert analysis["citations"][0]["source_url"] == url

    def test_beleid_malformed_url_removed(self) -> None:
        """A beleid citation with a mangled URL must have its source_url deleted."""
        bad_url = "https://capelleaandenijssel.begrotingsapp.nl/begroting-2024/WRONG"
        assert not _BELEID_URL_RE.match(bad_url)
        analysis = _make_v2_analysis(citations=[
            {"source": "beleid", "source_url": bad_url},
        ])
        stripped = OhrsExecutor._strip_fabricated_source_urls(analysis)
        assert stripped == 1
        assert "source_url" not in analysis["citations"][0]

    def test_no_raise_on_empty_dict(self) -> None:
        """Must not raise when both ``sections`` and ``citations`` are absent."""
        analysis: dict[str, Any] = {}
        result = OhrsExecutor._strip_fabricated_source_urls(analysis)
        assert result == 0

    def test_legacy_v1_behaviour_intact(self) -> None:
        """v1 sections[*].tool_output.data rows must still be processed."""
        analysis = {
            "sections": [
                {
                    "source": "cbs",
                    "tool_output": {
                        "data": [
                            {"doc_type": "table", "source_url": "https://evil.example/fabricated"},
                        ],
                    },
                }
            ]
        }
        stripped = OhrsExecutor._strip_fabricated_source_urls(analysis)
        assert stripped == 1
        assert "source_url" not in analysis["sections"][0]["tool_output"]["data"][0]

    def test_mixed_v2_citations_multiple(self) -> None:
        """Multiple citations: some removed, some kept — count is correct."""
        url = _valid_beleid_url()
        analysis = _make_v2_analysis(citations=[
            {"source": "budget", "source_url": "https://some.made.up/url"},
            {"source": "beleid", "source_url": url},
            {"source": "beleid", "source_url": "https://bad.url/not-matching"},
            {"source": "cbs"},  # no source_url at all — untouched
        ])
        stripped = OhrsExecutor._strip_fabricated_source_urls(analysis)
        assert stripped == 2
        assert "source_url" not in analysis["citations"][0]
        assert analysis["citations"][1]["source_url"] == url
        assert "source_url" not in analysis["citations"][2]
        assert "source_url" not in analysis["citations"][3]


# ---------------------------------------------------------------------------
# _strip_empty_source_rows — v2 blocks
# ---------------------------------------------------------------------------

class TestStripEmptySourceRowsBlocks:
    """Unit tests for the v2 blocks branch of ``_strip_empty_source_rows``."""

    def test_chart_block_empty_row_removed(self) -> None:
        """An all-None/blank row in a chart block's spec.data must be dropped."""
        analysis = _make_v2_analysis(blocks=[
            {
                "type": "chart",
                "spec": {
                    "data": [
                        {"name": "Jeugdzorg", "value": 100},    # good row
                        {"name": None, "value": None},          # empty row
                        {"name": "", "title": ""},              # also empty
                    ],
                },
            }
        ])
        dropped = OhrsExecutor._strip_empty_source_rows(analysis)
        assert dropped == 2
        remaining = analysis["blocks"][0]["spec"]["data"]
        assert len(remaining) == 1
        assert remaining[0]["name"] == "Jeugdzorg"

    def test_table_block_empty_row_removed(self) -> None:
        """An all-None/blank row in a table block's data must be dropped."""
        analysis = _make_v2_analysis(blocks=[
            {
                "type": "table",
                "data": [
                    {"title": "Rapport 2023", "year": "2023"},
                    {"title": None, "year": None},
                    {},
                ],
            }
        ])
        dropped = OhrsExecutor._strip_empty_source_rows(analysis)
        assert dropped == 2
        remaining = analysis["blocks"][0]["data"]
        assert len(remaining) == 1
        assert remaining[0]["title"] == "Rapport 2023"

    def test_no_raise_on_no_blocks_no_sections(self) -> None:
        """Must not raise when neither ``blocks`` nor ``sections`` is present."""
        analysis: dict[str, Any] = {}
        result = OhrsExecutor._strip_empty_source_rows(analysis)
        assert result == 0

    def test_no_raise_on_empty_blocks_list(self) -> None:
        """Must not raise on an analysis dict with an empty blocks list."""
        analysis = _make_v2_analysis(blocks=[])
        result = OhrsExecutor._strip_empty_source_rows(analysis)
        assert result == 0

    def test_legacy_v1_sections_still_processed(self) -> None:
        """v1 sections[*].tool_output.data empty rows must still be dropped."""
        analysis = {
            "sections": [
                {
                    "tool_output": {
                        "data": [
                            {"doc_type": "begroting", "year": "2023"},
                            {"doc_type": None, "document": None},  # empty
                        ],
                    }
                }
            ]
        }
        dropped = OhrsExecutor._strip_empty_source_rows(analysis)
        assert dropped == 1
        assert len(analysis["sections"][0]["tool_output"]["data"]) == 1

    def test_numeric_value_not_treated_as_empty(self) -> None:
        """A row with a numeric (non-string) identity value must be kept."""
        analysis = _make_v2_analysis(blocks=[
            {
                "type": "table",
                "data": [
                    {"year": 2023},   # numeric year → identifying, not empty
                ],
            }
        ])
        dropped = OhrsExecutor._strip_empty_source_rows(analysis)
        assert dropped == 0


# ---------------------------------------------------------------------------
# CAPELLE_MESSAGE_ID in _AGENT_ENV_ALLOW
# ---------------------------------------------------------------------------

class TestCapelleMessageIdInEnvAllow:
    """Verify that CAPELLE_MESSAGE_ID is declared in the env allowlist constant."""

    def test_capelle_message_id_in_agent_env_allow(self, settings_factory: Any, tmp_path: Any) -> None:
        """
        ``_AGENT_ENV_ALLOW`` must include ``"CAPELLE_MESSAGE_ID"`` so the env
        scrub step forwards the message-id to the ohrs child process.

        Note: the full "no report on first turn returns conversational result"
        path is covered by
        ``test_fresh_ohrs_job_returns_conversational_result_when_analysis_json_missing``
        in ``test_ohrs_job_plugins.py``, which uses a fake ohrs subprocess to
        exercise the entire ``execute()`` control flow.
        """
        from capelle_platform.settings import Settings

        # Construct a minimal Settings and OhrsExecutor to inspect the constant
        # as it would be used at runtime — we reference it via the class scope
        # rather than through a live execute() call to avoid spawning a process.
        settings = settings_factory(
            ohrs_working_dir=tmp_path / "repo-agent",
            ohrs_jobs_dir=tmp_path / "jobs",
        )
        executor = OhrsExecutor(settings)  # noqa: F841 — just for import smoke-test

        # The allowlist is a module-local variable (set literal) assembled
        # inside execute().  We can verify it by grepping the source, but the
        # cleanest contract is: import the module and assert the string is
        # present in the source text, which is what the executor actually
        # executes.  This is intentionally lightweight — a full subprocess
        # integration test lives in test_ohrs_job_plugins.py.
        import inspect
        src = inspect.getsource(OhrsExecutor.execute)
        assert "CAPELLE_MESSAGE_ID" in src, (
            "CAPELLE_MESSAGE_ID was not found in the execute() source — "
            "it must be added to _AGENT_ENV_ALLOW and set in env."
        )


class TestStripEmptyRowsKeepsRealChartData:
    """Regression: v2 chart/table DATA rows must NOT be judged 'empty' by the
    legacy identity-key heuristic. A row like {'jaar': 2019, 'cohesie': 5.2}
    has no doc_type/document/year-identity keys but IS real data — dropping it
    wiped agent-produced charts/tables (prod incident on session 5a804d)."""

    def test_keeps_chart_and_table_data_rows(self) -> None:
        analysis: dict[str, Any] = {
            "schema_version": 2,
            "blocks": [
                {
                    "type": "chart",
                    "spec": {
                        "kind": "line",
                        "data": [
                            {"jaar": 2019, "cohesie": 5.2},
                            {"jaar": 2021, "cohesie": 5.5},
                            {"jaar": 2023, "cohesie": 5.8},
                        ],
                        "columns": [{"key": "jaar"}, {"key": "cohesie"}],
                    },
                },
                {
                    "type": "table",
                    "data": [
                        {"indicator": "veiligheid", "2019": "33%", "2023": "37%"},
                        {"x": None, "y": ""},  # genuinely empty -> dropped
                    ],
                    "columns": [{"key": "indicator"}],
                },
            ],
        }
        dropped = OhrsExecutor._strip_empty_source_rows(analysis)
        assert dropped == 1
        assert len(analysis["blocks"][0]["spec"]["data"]) == 3
        assert len(analysis["blocks"][1]["data"]) == 1
