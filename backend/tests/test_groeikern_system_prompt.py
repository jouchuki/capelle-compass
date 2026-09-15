"""Tests for ``GroeikernModeConfig.build_system_prompt``.

These tests verify **structural** properties of the generated prompt —
they do not assert exact wording so that editorial improvements to the
prompt text do not require test updates. They check:

- The prompt is a non-empty string that embeds the requested output path.
- The v2 block-based output contract is documented (schema_version, blocks,
  citations, a sample of chart kinds).
- The ``capelle-ask`` interactive-clarification CLI is mentioned.
- The preserved safety rails are still present (HOST PROBING ban, CBS
  --series / --stats discipline, Dutch-language mandate).
- The old rigid mandate phrasing ("At least 3 sections") is gone.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the backend package root is on sys.path when pytest is run from any
# directory (mirrors the pattern in conftest.py).
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from capelle_platform.executor.modes.impl_groeikern import GroeikernModeConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OUTPUT_PATH = "/tmp/out/analysis.json"


def _make_config(settings_factory) -> GroeikernModeConfig:
    """Return a ``GroeikernModeConfig`` built from a minimal test Settings."""
    settings = settings_factory()
    return GroeikernModeConfig(settings)


def _prompt(settings_factory) -> str:
    """Convenience: build the prompt once and return it."""
    return _make_config(settings_factory).build_system_prompt(_OUTPUT_PATH)


# ---------------------------------------------------------------------------
# Basic shape
# ---------------------------------------------------------------------------


def test_prompt_is_non_empty_string(settings_factory) -> None:
    """The prompt must be a non-empty string."""
    result = _prompt(settings_factory)
    assert isinstance(result, str)
    assert len(result) > 0


def test_prompt_contains_output_path(settings_factory) -> None:
    """The requested output_path must appear verbatim in the prompt."""
    result = _prompt(settings_factory)
    assert _OUTPUT_PATH in result, (
        f"Expected output path '{_OUTPUT_PATH}' to appear in the prompt."
    )


# ---------------------------------------------------------------------------
# v2 block-based contract
# ---------------------------------------------------------------------------


def test_prompt_mentions_schema_version(settings_factory) -> None:
    """The prompt must document the schema_version field."""
    assert "schema_version" in _prompt(settings_factory)


def test_prompt_mentions_blocks(settings_factory) -> None:
    """The prompt must document the 'blocks' array."""
    assert "blocks" in _prompt(settings_factory)


def test_prompt_mentions_citations(settings_factory) -> None:
    """The prompt must document the 'citations' registry."""
    assert "citations" in _prompt(settings_factory)


@pytest.mark.parametrize("kind", ["boxplot", "violin", "histogram"])
def test_prompt_mentions_chart_kind(settings_factory, kind: str) -> None:
    """At least boxplot, violin, and histogram must appear in the chart catalog."""
    assert kind in _prompt(settings_factory), (
        f"Expected chart kind '{kind}' to be listed in the prompt."
    )


# ---------------------------------------------------------------------------
# capelle-ask interactive clarification
# ---------------------------------------------------------------------------


def test_prompt_mentions_capelle_ask(settings_factory) -> None:
    """The capelle-ask CLI for user clarification must be mentioned."""
    assert "capelle-ask" in _prompt(settings_factory)


# ---------------------------------------------------------------------------
# Preserved safety rails
# ---------------------------------------------------------------------------


def test_prompt_contains_host_probing_ban(settings_factory) -> None:
    """The HOST PROBING IS FORBIDDEN section must still be present."""
    prompt = _prompt(settings_factory)
    # Accept either the exact section heading or the key banned command.
    assert ("HOST PROBING" in prompt) or ("hostname" in prompt), (
        "Expected a host-probing ban marker in the prompt."
    )


def test_prompt_contains_cbs_discipline_markers(settings_factory) -> None:
    """CBS tool-call discipline flags --series and --stats must be present."""
    prompt = _prompt(settings_factory)
    assert "--series" in prompt, "Expected '--series' discipline marker."
    assert "--stats" in prompt, "Expected '--stats' discipline marker."


def test_prompt_contains_dutch_language_rule(settings_factory) -> None:
    """The Dutch-language mandate must appear in the prompt."""
    prompt = _prompt(settings_factory)
    assert ("DUTCH" in prompt) or ("Dutch" in prompt), (
        "Expected a Dutch-language rule in the prompt."
    )


# ---------------------------------------------------------------------------
# Old rigid mandate must be absent
# ---------------------------------------------------------------------------


def test_prompt_does_not_contain_at_least_3_sections(settings_factory) -> None:
    """The old 'At least 3 sections' rigid mandate must be removed."""
    assert "At least 3 sections" not in _prompt(settings_factory), (
        "Found the old rigid 'At least 3 sections' mandate — it should be absent."
    )


# ---------------------------------------------------------------------------
# Report title + web research
# ---------------------------------------------------------------------------


def test_prompt_documents_report_title_field(settings_factory) -> None:
    """The output contract must ask for a real report title (not the query)."""
    prompt = _prompt(settings_factory)
    assert '"title"' in prompt
    assert "REPORT TITLE" in prompt


def test_prompt_documents_deep_web_research(settings_factory) -> None:
    """Web research must be present and instruct depth (fetch + triangulate)."""
    prompt = _prompt(settings_factory)
    assert "WebSearch" in prompt and "WebFetch" in prompt
    assert "TRIANGULATE" in prompt
    # web sources must be citable as source:"web"
    assert 'source:"web"' in prompt


def test_prompt_no_longer_bans_web(settings_factory) -> None:
    """The old WebFetch/WebSearch BAN must be gone for groeikern."""
    assert "are BANNED" not in _prompt(settings_factory)


def test_prompt_requires_source_url_for_web_citations(settings_factory) -> None:
    """A web citation must carry source_url — the prompt must mandate it."""
    prompt = _prompt(settings_factory)
    assert "source_url" in prompt
    assert 'source:"web" citation is INVALID without `source_url`' in prompt or \
           'INVALID without `source_url`' in prompt


# ---------------------------------------------------------------------------
# Metaprompt: the question-generation engine
# ---------------------------------------------------------------------------


def test_prompt_has_question_engine(settings_factory) -> None:
    """The 3–5-questions-after-every-action engine must be present."""
    prompt = _prompt(settings_factory)
    assert "3-5" in prompt and "questions" in prompt.lower()
    # the standing decision-relevance gate
    assert "important to make any decision" in prompt


def test_prompt_report_is_residue_not_goal(settings_factory) -> None:
    """The prompt must frame the report as a byproduct of reasoning."""
    assert "residue" in _prompt(settings_factory).lower()


def test_prompt_has_worked_thinking_trace(settings_factory) -> None:
    """A worked THINKING TRACE (not a sample report) must be embedded."""
    prompt = _prompt(settings_factory)
    assert "WORKED EXAMPLE" in prompt
    # a recognisable reasoning move from the trace
    assert "base years" in prompt
    # it is explicitly a trace, not output
    assert "trace, not a report" in prompt


def test_prompt_has_verification_branch(settings_factory) -> None:
    """'Is this number real?' verification + a verifier subagent must appear."""
    prompt = _prompt(settings_factory)
    assert "placeholder" in prompt.lower()
    assert "re-derive" in prompt.lower()
    assert "verifier" in prompt.lower()


def test_capelle_ask_reframed_to_user_intent(settings_factory) -> None:
    """capelle-ask is for hearing what the user wants — not an ask-first reflex."""
    prompt = _prompt(settings_factory)
    # new framing present
    assert "hearing what the user" in prompt.lower() or "what the user actually want" in prompt.lower()
    # old ask-first-by-default reflex removed
    assert "Open almost every request with one focused" not in prompt
    assert "DEFAULT to asking a clarifying follow-up BEFORE you dive in" not in prompt


def test_capelle_ask_mechanism_preserved(settings_factory) -> None:
    """The hard mechanics of capelle-ask must survive the reframe."""
    prompt = _prompt(settings_factory)
    assert "capelle-ask" in prompt
    assert "AskUserQuestion" in prompt  # still warns it is a no-op
    assert "ONLY tool call" in prompt or "only tool call" in prompt.lower()


def test_fanout_skill_is_preloaded(settings_factory) -> None:
    """The /fan-out playbook must be in required_skill_files (always loaded)."""
    cfg = _make_config(settings_factory)
    names = [p.name for p in cfg.required_skill_files(Path("/skills"))]
    assert "fan-out.md" in names


def test_prompt_inlines_concrete_fanout(settings_factory) -> None:
    """The concrete spawn template + worked decompositions must be INLINE in the
    prompt (the only always-in-context channel), not merely referenced — the
    /fan-out skill is open-on-demand and was empirically never opened."""
    prompt = _prompt(settings_factory)
    assert "/fan-out" in prompt  # skill still referenced for the full playbook
    # the concrete spawn template is present inline
    assert "Agent(description=" in prompt
    # both worked decompositions are present inline
    assert "FOUR parallel agents" in prompt
    assert "ONE agent per\n" in prompt or "ONE agent per gemeente" in prompt or "one agent per" in prompt.lower()


def test_fanout_is_concept_based_and_default(settings_factory) -> None:
    """Fan-out triggers on multiple CONCEPTS deserving sole focus, biased to agents."""
    prompt = _prompt(settings_factory)
    # the bias: use agents more often than not
    assert "MORE OFTEN THAN NOT" in prompt
    # concept-based trigger, not merely multi-entity / context-overflow
    assert "concept" in prompt.lower()
    assert "sole focus" in prompt.lower()
    # the engine itself points branches -> dedicated subagents
    assert "dedicated subagent" in prompt.lower()


# ---------------------------------------------------------------------------
# Finding-graph section (Task 4)
# ---------------------------------------------------------------------------


def test_prompt_contains_batch_add_command(settings_factory) -> None:
    """The batch add command and findings file must be documented inline."""
    prompt = _prompt(settings_factory)
    assert "capelle-graph add ./findings.json" in prompt
    assert "findings.json" in prompt


def test_prompt_contains_set_summary_command(settings_factory) -> None:
    """The set-summary command must appear in the prompt."""
    assert "set-summary" in _prompt(settings_factory)


def test_prompt_contains_all_six_edge_types(settings_factory) -> None:
    """All six edge types must be present in the prompt."""
    prompt = _prompt(settings_factory)
    for edge_type in ["causes", "explains", "controls", "tensions_with",
                      "depends_on", "decomposes_into"]:
        assert edge_type in prompt, (
            f"Expected edge type '{edge_type}' to be present in the prompt."
        )


def test_prompt_contains_counterfactual(settings_factory) -> None:
    """The word 'counterfactual' must appear (tied to causal discipline)."""
    assert "counterfactual" in _prompt(settings_factory)


def test_prompt_contains_null_node_ban(settings_factory) -> None:
    """A ban on null/padding nodes must appear with a distinctive phrase."""
    prompt = _prompt(settings_factory)
    # The section uses "no node" as the distinctive ban phrase.
    assert "no node" in prompt.lower()


def test_graph_md_is_preloaded(settings_factory) -> None:
    """graph.md must be in required_skill_files (preflight — mirrors fan-out.md)."""
    cfg = _make_config(settings_factory)
    names = [p.name for p in cfg.required_skill_files(Path("/skills"))]
    assert "graph.md" in names


def test_prompt_demands_node_substance(settings_factory) -> None:
    """Nodes must be mini-reports: substantive prose blocks, not one-liners."""
    prompt = _prompt(settings_factory)
    assert "mini-rapport" in prompt
    assert "150-300" in prompt
    assert "Bite-sized nodes are a FAILURE" in prompt


def test_prompt_demands_edge_content(settings_factory) -> None:
    """Edges are analytical claims: 2-4 sentence mechanism rationales."""
    prompt = _prompt(settings_factory)
    assert "edges carry content" in prompt.lower()
    assert "ANALYTICAL CLAIM" in prompt
    assert "2-4 sentences" in prompt or "2-4 zinnen" in prompt


def test_prompt_makes_graph_writing_mandatory(settings_factory) -> None:
    """Graph-writing is a NON-NEGOTIABLE sequence with a verifiable end gate."""
    prompt = _prompt(settings_factory)
    assert "NON-NEGOTIABLE sequence" in prompt
    assert "FIRST tool action AFTER the plan" in prompt
    assert "FAILED run" in prompt
    # the end gate: list --compact check before analysis.json
    assert "capelle-graph list --compact" in prompt
    # the self-check carries the graph gate too
    assert "FINDING-GRAPH gate" in prompt
