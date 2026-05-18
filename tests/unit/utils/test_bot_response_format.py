import pytest

from utils.bot_response_format import format_bot_response


@pytest.mark.unit
def test_format_bot_response_escapes_html_and_preserves_allowed_tags() -> None:
    result = format_bot_response("<b>Важно</b> <script>x</script> <em>курсив</em>")

    assert "<b>Важно</b>" in result
    assert "&lt;script&gt;x&lt;/script&gt;" in result
    assert "<i>курсив</i>" in result


@pytest.mark.unit
def test_format_bot_response_converts_markdown_and_bolds_main_phrase() -> None:
    result = format_bot_response("Короткий вывод. Детали ниже\nСрок: 5 лет\n- Валюта: рубли")

    assert result.startswith("<b>Короткий вывод.</b>")
    assert "<b>Срок:</b> 5 лет" in result
    assert "- <b>Валюта:</b> рубли" in result


@pytest.mark.unit
def test_format_bot_response_removes_markdown_table_syntax() -> None:
    source = "| Продукт | Срок |\n| --- | --- |\n| Fort Knox | 6 месяцев |"

    result = format_bot_response(source)

    assert "| --- |" not in result
    assert "<b>Продукт:" in result
    assert "Fort Knox" in result


@pytest.mark.unit
def test_format_bot_response_wraps_long_lines() -> None:
    result = format_bot_response("x " * 600)

    assert all(len(line) <= 900 for line in result.splitlines())
