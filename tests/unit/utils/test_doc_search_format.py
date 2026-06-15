import pytest

from utils.doc_search_format import (
    build_download_rank_patterns,
    extract_download_ranks,
    extract_loose_tail_ranks,
    parse_download_ranks,
    render_doc_list_html,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("скачай 1 и 3", [1, 3]),
        ("8,13", []),
        ("  4  ", [4]),
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
        ("8, 13", []),
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
def test_parse_download_ranks_respects_max_list_rank() -> None:
    download_re, ranks_only_re, _ = build_download_rank_patterns(10)

    assert download_re.match("8,3")
    assert ranks_only_re.match("  7  ")
    assert ranks_only_re.match("1 и 10")
    assert not ranks_only_re.match("8,13")
    assert not ranks_only_re.match("11")


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



