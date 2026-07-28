"""Smoke tests tying hybrid release features to agent prompts and MCP contracts."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.unit
def test_doc_search_prompt_requires_search_profile() -> None:
    text = (ROOT / "kb_storage/prompts/doc_search/doc_search_agent_prompt.md").read_text(
        encoding="utf-8"
    )

    lower_text = text.lower()
    assert 'search_profile="doc_search"' in text
    assert "режим гибридного поиска" in lower_text or "hybrid" in lower_text
    assert "context" in lower_text
    assert "is_relevant" in lower_text
    assert "must_not" in lower_text or "исключает" in lower_text or "filters" in lower_text
    assert "6 архив" in lower_text


@pytest.mark.unit
def test_kb_answer_prompt_requires_search_profile() -> None:
    text = (ROOT / "kb_storage/prompts/kb_answer/kb_answer_agent_prompt.md").read_text(
        encoding="utf-8"
    )

    lower_text = text.lower()
    assert "search_profile" in lower_text
    assert "kb_answer" in lower_text


@pytest.mark.unit
def test_dispatcher_prompt_doc_search_search_query_is_verbatim() -> None:
    text = (ROOT / "kb_storage/prompts/dispatcher/dispatcher_agent_prompt.md").read_text(
        encoding="utf-8"
    )

    lower_text = text.lower()
    assert "search_query" in lower_text
    assert "doc_search" in lower_text
    assert "не подставляй" in lower_text or "не заменяй" in lower_text or "дослов" in lower_text


@pytest.mark.unit
def test_doc_search_agent_fallback_mentions_search_profile() -> None:
    text = (ROOT / "agent/agents/doc_search_agent.py").read_text(encoding="utf-8")

    assert 'search_profile="doc_search"' in text


@pytest.mark.unit
def test_kb_answer_agent_fallback_mentions_search_profile() -> None:
    text = (ROOT / "agent/agents/kb_answer_agent.py").read_text(encoding="utf-8")

    assert 'search_profile="kb_answer"' in text
