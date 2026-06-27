import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_ack_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "bot" / "services" / "ack.py"
    spec = importlib.util.spec_from_file_location("bot.services.ack", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


ack = _load_ack_module()


@pytest.mark.unit
@pytest.mark.parametrize(
    "text,expected",
    [
        ("", False),
        ("/reset", False),
        ("/start", False),
        ("привет", False),
        ("Здравствуйте", False),
        ("что ты умеешь", False),
        ("что такое ГСС", True),
        ("дай презентер FN", True),
    ],
)
def test_should_send_ack(text, expected) -> None:
    assert ack.should_send_ack(text) is expected


@pytest.mark.unit
def test_turn_tracking_stale_guard() -> None:
    ack.ACTIVE_TURNS.clear()
    ack.register_turn("user-1", "turn-a")
    assert ack.is_turn_still_active("user-1", "turn-a") is True
    ack.register_turn("user-1", "turn-b")
    assert ack.is_turn_still_active("user-1", "turn-a") is False
    assert ack.is_turn_still_active("user-1", "turn-b") is True
    ack.invalidate_turn("user-1")
    assert ack.is_turn_still_active("user-1", "turn-b") is False
