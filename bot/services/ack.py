"""Quick-ack helpers and per-user turn tracking for stale-final guard."""
from __future__ import annotations

import re

# user_key (global_user_id) -> active turn_id
ACTIVE_TURNS: dict[str, str] = {}

_GREETING_RE = re.compile(
    r"^\s*(?:"
    r"привет|"
    r"здравств\w*|"
    r"добр(?:ый|ое|ая|ого|ую|ой)(?:\s+(?:день|утро|вечер))?|"
    r"hello|hi"
    r")\b",
    re.IGNORECASE,
)


def register_turn(user_key: str, turn_id: str) -> None:
    ACTIVE_TURNS[user_key] = turn_id


def invalidate_turn(user_key: str) -> None:
    ACTIVE_TURNS.pop(user_key, None)


def is_turn_still_active(user_key: str, turn_id: str) -> bool:
    return ACTIVE_TURNS.get(user_key) == turn_id


_CAPABILITIES_RE = re.compile(
    r"^\s*(?:"
    r"что\s+(?:ты\s+)?(?:умеешь|можешь)|"
    r"чем\s+(?:ты\s+)?(?:можешь\s+)?помочь|"
    r"какие\s+(?:у\s+тебя\s+)?возможности|"
    r"на\s+что\s+(?:ты\s+)?способен"
    r")\s*[?.!]*\s*$",
    re.IGNORECASE,
)


def should_send_ack(user_text: str) -> bool:
    """Heuristic: generic ack before ADK when route is not yet known."""
    text = (user_text or "").strip()
    if not text:
        return False
    lower = text.lower()
    if lower.startswith("/"):
        return False
    if _GREETING_RE.match(text):
        return False
    if _CAPABILITIES_RE.match(text):
        return False
    return True
