import pytest

from utils.channel_session import (
    LEGACY_CHANNEL,
    build_session_id,
    context_cache_key,
    parse_session_id,
    session_id_prefix,
)


@pytest.mark.unit
def test_build_and_parse_roundtrip() -> None:
    session_id = build_session_id("user-1", "telegram", "turn-9")
    assert session_id == "user-1::telegram::turn-9"
    parsed = parse_session_id(session_id)
    assert parsed.user_id == "user-1"
    assert parsed.channel == "telegram"
    assert parsed.turn_id == "turn-9"


@pytest.mark.unit
def test_telegram_and_web_have_different_cache_keys() -> None:
    tg = build_session_id("user-1", "telegram", "a")
    web = build_session_id("user-1", "web", "b")
    assert context_cache_key(tg) == "user-1::telegram"
    assert context_cache_key(web) == "user-1::web"
    assert context_cache_key(tg) != context_cache_key(web)


@pytest.mark.unit
def test_legacy_session_id_does_not_use_channel_from_uuid_prefix() -> None:
    parsed = parse_session_id("user-1_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert parsed.user_id == "user-1"
    assert parsed.channel == LEGACY_CHANNEL


@pytest.mark.unit
def test_session_id_prefix_for_like_query() -> None:
    assert session_id_prefix("user-1", "max") == "user-1::max::"
