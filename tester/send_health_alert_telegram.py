#!/usr/bin/env python3
"""Send application health trigger alert to Telegram via Bot API."""

from __future__ import annotations

import argparse
import os
import sys

import requests

TELEGRAM_MAX_MESSAGE_LEN = 4096


def build_message(
    *,
    alert_text: str,
    env_name: str,
    build_url: str,
    adk_api_base: str,
) -> str:
    text = (
        f"[ALERT] chatbot-{env_name} is malfunctioning\n\n"
        f"{alert_text.strip()}\n\n"
        f"Build URL: {build_url}\n"
        f"ADK_API_BASE: {adk_api_base}"
    )
    if len(text) > TELEGRAM_MAX_MESSAGE_LEN:
        return text[: TELEGRAM_MAX_MESSAGE_LEN - 3] + "..."
    return text


def send_telegram_message(*, token: str, chat_id: str, text: str, proxy: str | None) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    proxies = None
    if proxy:
        proxies = {"http": proxy, "https": proxy}

    response = requests.post(
        url,
        json={"chat_id": chat_id, "text": text},
        timeout=30,
        proxies=proxies,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API error: {payload}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Send health-check trigger alert to Telegram.")
    parser.add_argument("--alert-file", required=True)
    parser.add_argument("--env-name", required=True)
    parser.add_argument("--build-url", required=True)
    parser.add_argument("--adk-api-base", required=True)
    args = parser.parse_args()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required", file=sys.stderr)
        return 2

    alert_path = os.path.expanduser(args.alert_file)
    alert_text = open(alert_path, encoding="utf-8").read()
    message = build_message(
        alert_text=alert_text,
        env_name=args.env_name,
        build_url=args.build_url,
        adk_api_base=args.adk_api_base,
    )

    proxy = os.getenv("TELEGRAM_PROXY", "").strip() or None
    send_telegram_message(token=token, chat_id=chat_id, text=message, proxy=proxy)
    print("Telegram alert sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
