import ast
from pathlib import Path
import types

import pytest


def _load_handle_product_kit_action(extra_globals: dict):
    file_path = Path(__file__).resolve().parents[3] / "bot" / "services" / "handlers.py"
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "handle_product_kit_action"
    ]
    module = ast.Module(body=selected, type_ignores=[])
    namespace = {"__builtins__": __builtins__}
    namespace.update(extra_globals)
    exec(compile(module, str(file_path), "exec"), namespace)
    return namespace["handle_product_kit_action"]


class _FakeEventLogger:
    def __init__(self):
        self.events = []

    async def log_event(self, **kwargs):
        self.events.append(kwargs)


class _FakeBotResponse:
    def __init__(self):
        self.messages = []
        self.docs = []

    async def send(self, text, menu=None, is_html=True, is_doc=None):
        if is_doc:
            self.docs.append(is_doc)
        else:
            self.messages.append(text)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_product_kit_action_sends_files() -> None:
    eventlogger = _FakeEventLogger()

    def fake_get_product_kit(product_id, product_name):
        return {
            "status": "ok",
            "files": [{"path": "/tmp/a.pdf", "name": "a.pdf", "size": 1}],
            "skipped_files": [],
        }

    handler = _load_handle_product_kit_action(
        {
            "get_product_kit": fake_get_product_kit,
            "eventlogger": eventlogger,
            "time": types.SimpleNamespace(time=lambda: 10.0),
        }
    )
    bot_res = _FakeBotResponse()

    result = await handler(
        bot_res=bot_res,
        bot_action={"type": "send_product_kit", "product_id": "2832", "product_name": "Fort Knox"},
        user_id=1,
        session_id="s1",
        turn_id="t1",
        start_time=9.0,
        platform="telegram",
    )

    assert result is True
    assert bot_res.docs == [{"path": "/tmp/a.pdf", "name": "a.pdf"}]
    assert [event["event_type"] for event in eventlogger.events] == [
        "document_download",
        "product_kit_sent",
    ]
    assert eventlogger.events[0]["payload"] == {
        "file_path": "/tmp/a.pdf",
        "text": "a.pdf",
        "doc_id": None,
        "rank": None,
        "source": "product_kit",
        "turn_id": "t1",
        "product_id": "2832",
        "product_name": "Fort Knox",
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_product_kit_action_sends_status_message_when_no_files() -> None:
    eventlogger = _FakeEventLogger()

    def fake_get_product_kit(product_id, product_name):
        return {
            "status": "not_found",
            "message": "Комплект для продукта пока не загружен.",
            "files": [],
            "skipped_files": [],
        }

    handler = _load_handle_product_kit_action(
        {
            "get_product_kit": fake_get_product_kit,
            "eventlogger": eventlogger,
            "time": types.SimpleNamespace(time=lambda: 10.0),
        }
    )
    bot_res = _FakeBotResponse()

    result = await handler(
        bot_res=bot_res,
        bot_action={"type": "send_product_kit", "product_id": "2832", "product_name": "Fort Knox"},
        user_id=1,
        session_id="s1",
        turn_id="t1",
        start_time=9.0,
        platform="telegram",
    )

    assert result is False
    assert bot_res.messages == ["Комплект для продукта пока не загружен."]
    assert eventlogger.events[0]["event_type"] == "product_kit_status"
