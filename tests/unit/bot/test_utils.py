import os
import sys
import types
from collections import OrderedDict

import pytest
import utils

os.environ.setdefault("BOT_START_MESSAGE_FILE", "C:/GitHub/asch-chat-bot/tests/.tmp/bot_start_message.md")
os.environ.setdefault("UPLOAD_NEWS", "C:/GitHub/asch-chat-bot/tests/.tmp")

utils.setup_logger = lambda *args, **kwargs: types.SimpleNamespace(
    info=lambda *a, **k: None,
    debug=lambda *a, **k: None,
    warning=lambda *a, **k: None,
    error=lambda *a, **k: None,
)

document_handler_stub = types.ModuleType("utils.document_handler")
document_handler_stub.DocumentHandler = type("DocumentHandler", (), {})
sys.modules["utils.document_handler"] = document_handler_stub

database_stub = types.ModuleType("bot.services.database")
database_stub.PostgresChatStore = type("PostgresChatStore", (), {})
sys.modules["bot.services.database"] = database_stub

from bot.services.config import Settings
from bot.services.utils import (
    html_to_telegram,
    markdown_to_safe_html,
    render_results,
    register_callback_path,
    split_message,
)


@pytest.mark.unit
def test_markdown_to_safe_html_escapes_html_and_converts_basic_markdown() -> None:
    source = "**bold** *italic* `code` [link](https://example.com) <b>x</b>"

    result = markdown_to_safe_html(source)

    assert "<b>bold</b>" in result
    assert "<i>italic</i>" in result
    assert "<code>code</code>" in result
    assert '<a href="https://example.com">link</a>' in result
    assert "&lt;b&gt;x&lt;/b&gt;" in result


@pytest.mark.unit
def test_render_results_delegates_to_shared_doc_list_renderer() -> None:
    items = [{"source_name": "Doc 1", "snippet": "Snippet"}]

    result = render_results(items, total=1, offset=0)

    assert "<b>1. Doc 1</b>" in result
    assert "Snippet" in result


@pytest.mark.unit
def test_split_message_splits_text_by_limit() -> None:
    result = split_message("abcdefghij", limit=4)

    assert result == ["abcd", "efgh", "ij"]


@pytest.mark.unit
def test_html_to_telegram_converts_and_strips_unsupported_tags() -> None:
    source = (
        "<h2>Header</h2>"
        "<p>First<br>Second</p>"
        "<ul><li>One</li><li>Two</li></ul>"
        "<strong>bold</strong><em>italic</em>"
        "<div>drop</div>"
        "&nbsp;&amp;"
    )

    result = html_to_telegram(source)

    assert "<b>Header</b>" in result
    assert "First\nSecond" in result
    assert "• One" in result
    assert "• Two" in result
    assert "<b>bold</b><i>italic</i>" in result
    assert "<div>" not in result
    assert "drop" in result
    assert " &" in result


@pytest.mark.unit
def test_html_to_telegram_returns_empty_string_for_empty_input() -> None:
    assert html_to_telegram("") == ""


@pytest.mark.unit
def test_register_callback_path_adds_value_to_callback_map(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Settings, "CALLBACK_MAP", OrderedDict())
    monkeypatch.setattr(Settings, "MAX_CALLBACK_ENTRIES", 3)

    path_id = register_callback_path("root/folder")

    assert path_id == "1"
    assert list(Settings.CALLBACK_MAP.items()) == [("1", "root/folder")]


@pytest.mark.unit
def test_register_callback_path_drops_oldest_entry_when_limit_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback_map = OrderedDict(
        [
            ("1", "first"),
            ("2", "second"),
        ]
    )
    monkeypatch.setattr(Settings, "CALLBACK_MAP", callback_map)
    monkeypatch.setattr(Settings, "MAX_CALLBACK_ENTRIES", 2)

    path_id = register_callback_path("third")

    assert path_id == "3"
    assert list(Settings.CALLBACK_MAP.items()) == [("2", "second"), ("3", "third")]
