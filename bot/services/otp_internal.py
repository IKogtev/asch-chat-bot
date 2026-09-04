"""Узкий внутренний OTP-send: секрет + только platform_user_id и код."""

from __future__ import annotations

import hashlib
import hmac
import os
import re

from fastapi import Header, HTTPException

OTP_CODE_RE = re.compile(r"^\d{4,6}$")
OTP_MESSAGE = (
    "Код для входа на сайт Насти: {code}\n"
    "Никому его не сообщайте."
)


def otp_internal_secret() -> str:
    return (os.getenv("OTP_INTERNAL_SECRET") or "").strip()


def secrets_match(given: str | None, expected: str) -> bool:
    left = hashlib.sha256((given or "").encode("utf-8")).digest()
    right = hashlib.sha256((expected or "").encode("utf-8")).digest()
    return hmac.compare_digest(left, right) and bool(expected)


def require_otp_sender(x_internal_otp_key: str | None = Header(default=None, alias="X-Internal-Otp-Key")) -> None:
    expected = otp_internal_secret()
    if not secrets_match(x_internal_otp_key, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


def otp_text(code: str) -> str:
    return OTP_MESSAGE.format(code=code)


async def send_otp_to_user(bot, source: str, platform_user_id: int, code: str) -> None:
    if bot is None:
        raise RuntimeError("bot_unavailable")
    if not OTP_CODE_RE.match(code):
        raise ValueError("invalid_code")
    text = otp_text(code)
    if source == "telegram":
        await bot.send_message(platform_user_id, text)
        return
    await bot.send_message(user_id=platform_user_id, text=text)
