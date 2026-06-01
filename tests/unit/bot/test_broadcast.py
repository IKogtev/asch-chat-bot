import ast
import asyncio
import types
from pathlib import Path
from typing import List, Optional

import pytest


def _load_symbols(file_path: Path, names: list[str], extra_globals: dict):
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    module = ast.Module(body=selected, type_ignores=[])
    namespace = {"__builtins__": __builtins__}
    namespace.update(extra_globals)
    exec(compile(module, str(file_path), "exec"), namespace)
    return namespace


def _logger():
    return types.SimpleNamespace(
        info=lambda *a, **k: None,
        debug=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )


class _EventLogger:
    async def log_event(self, **kwargs):
        return None


class _BufferedInputFile:
    def __init__(self, content, filename: str):
        self.content = content
        self.filename = filename


_symbols = _load_symbols(
    Path(__file__).resolve().parents[3] / "bot" / "services" / "broadcast.py",
    ["send_now", "get_filtered_users"],
    {
        "List": List,
        "Optional": Optional,
        "asyncio": asyncio,
        "eventlogger": _EventLogger(),
        "logger": _logger(),
        "random": types.SimpleNamespace(random=lambda: 0),
        "split_message": lambda text, limit=4000: [text[i:i + limit] for i in range(0, len(text), limit)] if text else [],
        "BufferedInputFile": _BufferedInputFile,
    },
)

send_now = _symbols["send_now"]
get_filtered_users = _symbols["get_filtered_users"]


class _SubscriberStore:
    def __init__(self, users):
        self.users = users

    async def get_all_with_groups(self):
        return self.users


class _FakeBot:
    def __init__(self):
        self.messages = []
        self.documents = []
        self.photos = []

    async def send_message(self, user_id, text, parse_mode=None):
        self.messages.append((user_id, text, parse_mode))

    async def send_document(self, user_id, file):
        self.documents.append((user_id, file.filename))

    async def send_photo(self, user_id, file):
        self.photos.append((user_id, file.filename))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_filtered_users_returns_all_and_group_specific_lists() -> None:
    store = _SubscriberStore(
        [
            {"user_id": 1, "manager_group": True, "coach_group": False},
            {"user_id": 2, "manager_group": False, "coach_group": True},
        ]
    )

    all_users, all_count = await get_filtered_users(store, "all")
    managers, _ = await get_filtered_users(store, "manager_group")
    coaches, _ = await get_filtered_users(store, "coach_group")

    assert all_users == [1, 2]
    assert all_count == 2
    assert managers == [1]
    assert coaches == [2]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_now_sends_text_and_files(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(_symbols["asyncio"], "sleep", fake_sleep)

    bot = _FakeBot()
    holder = types.SimpleNamespace(instance=bot)
    store = _SubscriberStore([{"user_id": 10, "manager_group": True, "coach_group": False}])

    result = await send_now(
        text="hello",
        file_data=[
            ("pic.png", "image/png", b"img"),
            ("doc.pdf", "application/pdf", b"doc"),
        ],
        target_group="all",
        bot_holder=holder,
        subscriber_store=store,
    )

    assert result == {
        "sent": 1,
        "failed": 0,
        "total": 1,
        "success_rate": 100.0,
        "errors": [],
        "no_users": False,
    }
    assert bot.messages == [(10, "hello", "HTML")]
    assert bot.photos == [(10, "pic.png")]
    assert bot.documents == [(10, "doc.pdf")]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_now_skips_delivery_when_bot_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(_symbols["asyncio"], "sleep", fake_sleep)

    holder = types.SimpleNamespace(instance=None)
    store = _SubscriberStore([{"user_id": 10, "manager_group": True, "coach_group": False}])

    result = await send_now(
        text="hello",
        file_data=[],
        target_group="all",
        bot_holder=holder,
        subscriber_store=store,
    )

    assert result == {
        "sent": 0,
        "failed": 1,
        "total": 1,
        "success_rate": 0.0,
        "errors": ["user 10: bot reconnecting"],
        "no_users": False,
    }
