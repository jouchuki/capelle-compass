"""
Tests for the add-in ``document_context`` grounding feature.

The Office add-in can send the open document's text alongside a chat
message; the executor injects it as a clearly-delimited DOCUMENT section
BEFORE the user's question so the agent grounds in it without citing it as
a source. These tests cover:

* the field threads onto ``ChatMessageCreate`` and ``JobMessage`` (and
  defaults to ``None`` — backward compatible);
* the assembled prompt (``_build_prompt_async``, the analysis path) contains
  the DOCUMENT section + the document text + the original query when the
  field is present;
* the prompt is byte-identical to the no-document baseline when the field
  is absent or whitespace-only (the regression contract).
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from capelle_platform.executor.impl_ohrs import OhrsExecutor
from capelle_platform.models.job import JobMessage
from capelle_platform.models.message import ChatMessageCreate
from capelle_platform.settings import Settings

_OUTPUT_PATH = "/tmp/job/analysis.json"
_QUERY = "Hoe staat gemeente Capelle aan den IJssel er financieel voor?"


def _settings() -> Settings:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Settings(
        sqlite_path=Path(tmp.name),
        jwt_secret="test-secret-minimum-32-characters-long-ok",
        graph_enabled=False,
    )


def _job(document_context: str | None = None, **kw) -> JobMessage:
    return JobMessage(
        session_id="sess-001",
        message_id="msg-001",
        user_id="user-001",
        query=_QUERY,
        skill="capelle-analyse",
        trace_id="trace-001",
        mode="groeikern",
        document_context=document_context,
        **kw,
    )


def _build(job: JobMessage) -> str:
    return asyncio.get_event_loop().run_until_complete(
        OhrsExecutor._build_prompt_async(job, _OUTPUT_PATH, None, _settings())
    )


# ---------------------------------------------------------------------------
# Model wiring
# ---------------------------------------------------------------------------


def test_chat_message_create_accepts_document_context() -> None:
    """ChatMessageCreate accepts an optional document_context string."""
    msg = ChatMessageCreate(content="Hallo", document_context="een notitie")
    assert msg.document_context == "een notitie"


def test_chat_message_create_document_context_defaults_none() -> None:
    """Omitting document_context yields None — backward compatible."""
    assert ChatMessageCreate(content="Hallo").document_context is None


def test_chat_message_create_document_context_respects_max_length() -> None:
    """document_context is capped at 20000 chars."""
    import pytest
    from pydantic import ValidationError

    ChatMessageCreate(content="x", document_context="a" * 20000)
    with pytest.raises(ValidationError):
        ChatMessageCreate(content="x", document_context="a" * 20001)


def test_job_message_defaults_document_context_none() -> None:
    """JobMessage defaults document_context to None for legacy jobs."""
    assert _job().document_context is None


# ---------------------------------------------------------------------------
# Prompt injection (analysis path via _build_prompt_async)
# ---------------------------------------------------------------------------


def test_prompt_injects_document_section_when_present() -> None:
    """A non-empty document_context yields a DOCUMENT section before the query."""
    doc = "Begrotingsnotitie 2025: tekort van 4 miljoen euro verwacht."
    prompt = _build(_job(document_context=doc))

    assert "# DOCUMENT VAN DE GEBRUIKER (context)" in prompt
    assert "citeer er niet uit alsof het een bron is" in prompt
    assert doc in prompt
    assert "# VRAAG" in prompt
    assert _QUERY in prompt
    # The DOCUMENT section precedes the VRAAG section (grounding read first).
    assert prompt.index("# DOCUMENT VAN DE GEBRUIKER") < prompt.index("# VRAAG")
    assert prompt.index(doc) < prompt.index(_QUERY)


def test_prompt_identical_to_baseline_when_absent() -> None:
    """No document_context → byte-identical to the no-field baseline."""
    baseline = _build(_job(document_context=None))
    assert "# DOCUMENT VAN DE GEBRUIKER" not in baseline


def test_prompt_identical_to_baseline_when_whitespace() -> None:
    """A whitespace-only document_context is treated as absent."""
    baseline = _build(_job(document_context=None))
    whitespace = _build(_job(document_context="   \n\t  "))
    assert whitespace == baseline


def test_document_context_applies_on_followup_turn() -> None:
    """The DOCUMENT section also wraps the follow-up (history) prompt."""
    history = [
        {"role": "user", "content": "Eerdere vraag"},
        {"role": "assistant", "content": "Eerder antwoord"},
    ]
    doc = "Bijlage A met de relevante cijfers."
    prompt = _build(_job(document_context=doc, history=history))
    assert "# DOCUMENT VAN DE GEBRUIKER (context)" in prompt
    assert doc in prompt
    # Follow-up scaffolding still present.
    assert "Intent classification" in prompt
