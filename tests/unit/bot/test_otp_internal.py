import pytest

from bot.services.otp_internal import otp_text, secrets_match


@pytest.mark.unit
def test_secrets_match_rejects_empty_expected() -> None:
    assert secrets_match("anything", "") is False


@pytest.mark.unit
def test_secrets_match_accepts_equal() -> None:
    assert secrets_match("abc", "abc") is True
    assert secrets_match("abc", "xyz") is False


@pytest.mark.unit
def test_otp_text_contains_code_only_as_digits() -> None:
    text = otp_text("482910")
    assert "482910" in text
    assert "Насти" in text
