"""Идентичность ADK-сессии: пользователь + канал + ход.

Формат: `{global_user_id}::{channel}::{turn_id}`
Старый формат `{global_user_id}_{turn_id}` читается как channel=legacy.
"""

from __future__ import annotations

from typing import NamedTuple

ALLOWED_CHANNELS = frozenset({"telegram", "max", "web"})
LEGACY_CHANNEL = "legacy"


class SessionIdentity(NamedTuple):
    user_id: str
    channel: str
    turn_id: str | None


def normalize_channel(channel: str | None) -> str:
    value = (channel or "").strip().lower()
    if value in ALLOWED_CHANNELS:
        return value
    return LEGACY_CHANNEL


def build_session_id(user_id: str, channel: str, turn_id: str) -> str:
    return f"{user_id}::{normalize_channel(channel)}::{turn_id}"


def session_id_prefix(user_id: str, channel: str) -> str:
    return f"{user_id}::{normalize_channel(channel)}::"


def parse_session_id(session_id: str | None) -> SessionIdentity:
    raw = (session_id or "").strip()
    if not raw:
        return SessionIdentity(user_id="", channel=LEGACY_CHANNEL, turn_id=None)
    parts = raw.split("::")
    if len(parts) >= 3 and parts[0] and parts[1]:
        return SessionIdentity(
            user_id=parts[0],
            channel=normalize_channel(parts[1]),
            turn_id=parts[2] or None,
        )
    user_id = raw.split("_", 1)[0]
    return SessionIdentity(user_id=user_id, channel=LEGACY_CHANNEL, turn_id=None)


def context_cache_key(session_id: str | None) -> str:
    identity = parse_session_id(session_id)
    if not identity.user_id:
        return ""
    return f"{identity.user_id}::{identity.channel}"
