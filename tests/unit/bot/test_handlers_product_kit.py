import ast
from pathlib import Path
import types
import asyncio

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


def _load_bot_response(extra_globals: dict):
    file_path = Path(__file__).resolve().parents[3] / "bot" / "services" / "handlers.py"
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BotResponse"
    ]
    module = ast.Module(body=selected, type_ignores=[])
    namespace = {"__builtins__": __builtins__}
    namespace.update(extra_globals)
    exec(compile(module, str(file_path), "exec"), namespace)
    return namespace["BotResponse"]


class _FakeEventLogger:
    def __init__(self):
        self.events = []

    async def log_event(self, **kwargs):
        self.events.append(kwargs)


def _build_logger():
    return types.SimpleNamespace(
        info=lambda *a, **k: None,
        debug=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )


class _FakeBotResponse:
    def __init__(self):
        self.messages = []
        self.docs = []

    async def send(self, text, menu=None, is_html=True, is_doc=None):
        if is_doc:
            self.docs.append(is_doc)
        else:
            self.messages.append(text)


class _FakeFSInputFile:
    def __init__(self, path, filename=None):
        self.path = path
        self.filename = filename


class _FakeTelegramMessage:
    def __init__(self):
        self.documents = []

    async def answer_document(self, document):
        self.documents.append(document)


def _load_cancel_user_request(extra_globals: dict):
    file_path = Path(__file__).resolve().parents[3] / "bot" / "services" / "handlers.py"
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "cancel_user_request"
    ]
    module = ast.Module(body=selected, type_ignores=[])
    namespace = {"__builtins__": __builtins__}
    namespace.update(extra_globals)
    exec(compile(module, str(file_path), "exec"), namespace)
    return namespace["cancel_user_request"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bot_response_sends_telegram_document_from_message() -> None:
    bot_response_cls = _load_bot_response({"FSInputFile": _FakeFSInputFile})
    message = _FakeTelegramMessage()
    bot_response = bot_response_cls(message, "telegram")

    await bot_response.send("", is_doc={"path": "/tmp/a.pdf", "name": "a.pdf"})

    assert len(message.documents) == 1
    assert message.documents[0].path == "/tmp/a.pdf"
    assert message.documents[0].filename == "a.pdf"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_product_kit_action_sends_files() -> None:
    eventlogger = _FakeEventLogger()

    def fake_get_product_kit(product_code, product_name, folder_kit, folder_kit_root=None):
        assert product_code == "2832"
        assert folder_kit == "Fort Knox (2832)"
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
            "logger": _build_logger(),
        }
    )
    bot_res = _FakeBotResponse()

    result = await handler(
        bot_res=bot_res,
        bot_action={
            "type": "send_product_kit",
            "product_code": "2832",
            "product_name": "Fort Knox",
            "folder_kit": "Fort Knox (2832)",
        },
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
        "product_code": "2832",
        "product_name": "Fort Knox",
        "folder_kit": "Fort Knox (2832)",
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_product_kit_action_sends_status_message_when_no_files() -> None:
    eventlogger = _FakeEventLogger()

    def fake_get_product_kit(product_code, product_name, folder_kit, folder_kit_root=None):
        assert product_code == "2832"
        assert folder_kit == ""
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
            "logger": _build_logger(),
        }
    )
    bot_res = _FakeBotResponse()

    result = await handler(
        bot_res=bot_res,
        bot_action={"type": "send_product_kit", "product_code": "2832", "product_name": "Fort Knox"},
        user_id=1,
        session_id="s1",
        turn_id="t1",
        start_time=9.0,
        platform="telegram",
    )

    assert result is False
    assert bot_res.messages == ["Комплект для продукта пока не загружен."]
    assert eventlogger.events[0]["event_type"] == "product_kit_status"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_user_request_cancels_active_task_and_clears_active_requests() -> None:
    """Test that cancel_user_request cancels active tasks and removes them from ACTIVE_REQUESTS."""
    active_requests = {}
    cancel_user_request = _load_cancel_user_request(
        {
            "ACTIVE_REQUESTS": active_requests,
            "logger": _build_logger(),
            "asyncio": asyncio,
        }
    )

    async def long_running():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise

    running_task = asyncio.create_task(long_running())
    await asyncio.sleep(0)
    active_requests["user-1"] = running_task

    result = await cancel_user_request("user-1")

    assert result is True
    assert running_task.cancelled() is True
    assert "user-1" not in active_requests


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_user_request_returns_false_when_no_active_task() -> None:
    """Test that cancel_user_request returns False when there's no active task."""
    active_requests = {}
    cancel_user_request = _load_cancel_user_request(
        {
            "ACTIVE_REQUESTS": active_requests,
            "logger": _build_logger(),
            "asyncio": asyncio,
        }
    )

    result = await cancel_user_request("missing-user")

    assert result is False
