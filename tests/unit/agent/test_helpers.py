import importlib.util
from pathlib import Path

import pytest


helpers_path = Path(__file__).resolve().parents[3] / "agent" / "helpers.py"
spec = importlib.util.spec_from_file_location("agent_helpers_for_tests", helpers_path)
helpers = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
spec.loader.exec_module(helpers)

deduplicate_results = helpers.deduplicate_results
extract_json = helpers.extract_json
format_bot_contract_search_results = helpers.format_bot_contract_search_results
format_bot_search_meta = helpers.format_bot_search_meta
format_reject_answer = helpers.format_reject_answer
format_search_results_contract = helpers.format_search_results_contract
format_text_answer = helpers.format_text_answer
truncate_for_log = helpers.truncate_for_log


@pytest.mark.unit
def test_extract_json_parses_plain_json() -> None:
    result = extract_json('{"status":"ok","route":"kb_answer"}')

    assert result == {"status": "ok", "route": "kb_answer"}


@pytest.mark.unit
def test_extract_json_parses_json_inside_markdown_block() -> None:
    text = '```json\n{"status":"ok","route":"doc_search"}\n```'

    result = extract_json(text)

    assert result == {"status": "ok", "route": "doc_search"}


@pytest.mark.unit
def test_extract_json_raises_for_invalid_json() -> None:
    with pytest.raises(ValueError, match="Invalid JSON"):
        extract_json('```json\n{"status": }\n```')


@pytest.mark.unit
def test_extract_json_raises_when_json_not_found() -> None:
    with pytest.raises(ValueError, match="JSON object not found"):
        extract_json("no json here")


@pytest.mark.unit
def test_format_search_results_contract_wraps_payload() -> None:
    results = [{"document_id": "doc-1", "score": 0.9}]

    result = format_search_results_contract("Найдено", results)

    assert result.startswith("<bot_contract>")
    assert '"type": "search_results"' in result
    assert '"message": "Найдено"' in result
    assert '"document_id": "doc-1"' in result
    assert result.endswith("</bot_contract>")


@pytest.mark.unit
def test_format_bot_contract_search_results_wraps_results() -> None:
    results = [{"document_id": "doc-2"}]

    result = format_bot_contract_search_results(results)

    assert result.startswith("<bot_contract>")
    assert '"mode": "search_results"' in result
    assert '"document_id": "doc-2"' in result
    assert result.endswith("</bot_contract>")


@pytest.mark.unit
def test_format_bot_search_meta_wraps_payload() -> None:
    result = format_bot_search_meta({"search_id": "42", "shown_count": 3})

    assert result == '<bot_search_meta>{"search_id": "42", "shown_count": 3}</bot_search_meta>'


@pytest.mark.unit
def test_format_text_and_reject_answer_strip_whitespace() -> None:
    assert format_text_answer("  ответ  ") == "ответ"
    assert format_reject_answer("  отказ  ") == "отказ"


@pytest.mark.unit
def test_deduplicate_results_keeps_item_with_best_score_per_document() -> None:
    items = [
        {"document_id": "doc-1", "score": 0.4, "snippet": "old"},
        {"document_id": "doc-1", "score": 0.8, "snippet": "best"},
        {"document_id": "doc-2", "score": 0.3},
    ]

    result = deduplicate_results(items)

    by_id = {item["document_id"]: item for item in result}
    assert len(result) == 2
    assert by_id["doc-1"]["snippet"] == "best"
    assert by_id["doc-2"]["score"] == 0.3


@pytest.mark.unit
def test_deduplicate_results_skips_items_without_document_id() -> None:
    items = [
        {"score": 0.5},
        {"document_id": "doc-1", "score": 0.2},
    ]

    result = deduplicate_results(items)

    assert result == [{"document_id": "doc-1", "score": 0.2}]


@pytest.mark.unit
def test_truncate_for_log_returns_empty_string_for_empty_input() -> None:
    assert truncate_for_log("", max_length=10) == ""
    assert truncate_for_log(None, max_length=10) == ""


@pytest.mark.unit
def test_truncate_for_log_returns_original_when_short_enough() -> None:
    assert truncate_for_log("short", max_length=10) == "short"


@pytest.mark.unit
def test_truncate_for_log_truncates_long_text() -> None:
    assert truncate_for_log("abcdefghij", max_length=5) == "abcde..."
