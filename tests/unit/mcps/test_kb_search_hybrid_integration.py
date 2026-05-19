"""Smoke tests tying hybrid release features to agent prompts and MCP contracts."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.unit
def test_doc_search_prompt_requires_search_profile() -> None:
    text = (ROOT / "kb_storage/prompts/doc_search/doc_search_agent_prompt.md").read_text(
        encoding="utf-8"
    )

    assert 'search_profile="doc_search"' in text
    assert "режим гибридного поиска" in text.lower() or "hybrid" in text
    assert "каждый документ из CONTEXT" in text
    assert "is_relevant" in text
    assert "must_not" in text or "исключает" in text.lower()
    assert "5 Архив" in text


@pytest.mark.unit
def test_kb_answer_prompt_requires_search_profile() -> None:
    text = (ROOT / "kb_storage/prompts/kb_answer/kb_answer_agent_prompt.md").read_text(
        encoding="utf-8"
    )

    assert 'search_profile="kb_answer"' in text


@pytest.mark.unit
def test_dispatcher_prompt_doc_search_search_query_is_verbatim() -> None:
    text = (ROOT / "kb_storage/prompts/dispatcher/dispatcher_agent_prompt.md").read_text(
        encoding="utf-8"
    )

    assert "дословн" in text.lower()
    assert "doc_search" in text


@pytest.mark.unit
def test_doc_search_agent_fallback_mentions_search_profile() -> None:
    text = (ROOT / "agent/agents/doc_search_agent.py").read_text(encoding="utf-8")

    assert 'search_profile="doc_search"' in text


@pytest.mark.unit
def test_kb_answer_agent_fallback_mentions_search_profile() -> None:
    text = (ROOT / "agent/agents/kb_answer_agent.py").read_text(encoding="utf-8")

    assert 'search_profile="kb_answer"' in text
