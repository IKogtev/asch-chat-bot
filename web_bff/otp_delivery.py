"""Доставка OTP в Telegram/MAX через внутренний HTTP ботов."""

from __future__ import annotations

from typing import Any

import aiohttp

from utils.logger import setup_logger
from web_bff.config import WebBffSettings

logger = setup_logger("web_bff_otp", "web_bff.log")

_PLATFORM_URL = {
    "telegram": "bot_telegram_api",
    "max": "bot_max_api",
}


async def deliver_otp_code(
    settings: WebBffSettings,
    accounts: list[dict[str, Any]],
    code: str,
) -> int:
    secret = (settings.otp_internal_secret or "").strip()
    if not secret:
        logger.error("OTP_INTERNAL_SECRET is empty, skip messenger delivery")
        return 0
    sent = 0
    headers = {"X-Internal-Otp-Key": secret}
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for account in accounts:
            platform = (account.get("platform") or "").strip().lower()
            attr = _PLATFORM_URL.get(platform)
            if not attr:
                continue
            base = getattr(settings, attr, "") or ""
            if not base:
                logger.warning("otp delivery skipped: no url for platform=%s", platform)
                continue
            url = f"{base.rstrip('/')}/internal/otp"
            payload = {
                "platform_user_id": int(account["platform_user_id"]),
                "code": code,
            }
            try:
                async with session.post(url, json=payload, headers=headers) as response:
                    if response.status >= 400:
                        body = (await response.text())[:200]
                        logger.warning(
                            "otp delivery http=%s platform=%s body=%r",
                            response.status,
                            platform,
                            body,
                        )
                        continue
                    sent += 1
            except aiohttp.ClientError as exc:
                logger.warning("otp delivery failed platform=%s error=%s", platform, exc)
    logger.info("otp delivered channels=%s", sent)
    return sent
