import pytest

from utils.doc_search_format import (
    extract_bot_search_meta,
    extract_document_id_lines,
    extract_download_ranks,
    extract_loose_tail_ranks,
    parse_download_ranks,
    render_doc_list_html,
    strip_bot_search_meta,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("скачай 1 и 3", [1, 3]),
        ("8,13", [8, 13]),
        ("  7  ", [7]),
        ("документ 2", [2]),
        ("ничего похожего", []),
        ("", []),
        (None, []),
    ],
)
def test_parse_download_ranks(text, expected) -> None:
    assert parse_download_ranks(text) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 и 5", [1, 5]),
        ("8, 13", [8, 13]),
        ("Fort Knox 1 и 5", [1, 5]),
        ("слишком длинная строка для loose tail 1 и 5", []),
        ("1", []),
    ],
)
def test_extract_loose_tail_ranks(text: str, expected: list[int]) -> None:
    assert extract_loose_tail_ranks(text) == expected


@pytest.mark.unit
def test_extract_download_ranks_uses_extra_hint_when_main_text_not_match() -> None:
    result = extract_download_ranks("скачай документ", extra_hint="1 и 5")

    assert result == [1, 5]


@pytest.mark.unit
def test_extract_download_ranks_returns_empty_when_neither_text_nor_hint_match() -> None:
    result = extract_download_ranks("покажи документы", extra_hint="подсказка без рангов")

    assert result == []


@pytest.mark.unit
def test_render_doc_list_html_for_empty_items() -> None:
    assert render_doc_list_html([], total=0) == "Ничего не нашёл."


@pytest.mark.unit
def test_render_doc_list_html_escapes_and_truncates_items() -> None:
    items = [
        {
            "source_name": "<script>alert(1)</script>",
            "snippet": "x" * 181 + "\nsecond line",
        }
    ]

    result = render_doc_list_html(items, total=1)

    assert "<script>" not in result
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in result
    assert "x" * 177 + "..." in result
    assert "Напишите номер документа" in result


@pytest.mark.unit
def test_render_doc_list_html_contains_pagination_hint_when_not_all_items_shown() -> None:
    items = [{"source_name": "Doc 1", "snippet": "Snippet"}]

    result = render_doc_list_html(items, total=3, offset=0)

    assert "Показано 1 из 3" in result
    assert "<b>ещё</b>" in result


@pytest.mark.unit
def test_strip_and_extract_bot_search_meta() -> None:
    text = 'Ответ\n<bot_search_meta>{"search_id":"42","shown_count":2}</bot_search_meta>'

    assert strip_bot_search_meta(text) == "Ответ"
    assert extract_bot_search_meta(text) == {"search_id": "42", "shown_count": 2}


@pytest.mark.unit
def test_extract_bot_search_meta_returns_none_for_invalid_json() -> None:
    text = "<bot_search_meta>{not-json}</bot_search_meta>"

    assert extract_bot_search_meta(text) is None


@pytest.mark.unit
def test_extract_document_id_lines_returns_text_without_service_lines_and_all_ids() -> None:
    text = "\n".join(
        [
            "Первая строка",
            "document_id: abc-123",
            "document_id:XYZ",
            "Финальная строка",
        ]
    )

    clean_text, ids = extract_document_id_lines(text)

    assert clean_text == "Первая строка\nФинальная строка"
    assert ids == ["abc-123", "XYZ"]
