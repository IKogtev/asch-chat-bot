"""Нормализация телефона — та же логика, что у бота, без тяжёлого импорта handlers."""

from __future__ import annotations

import re


def normalize_phone(phone: str | None) -> str:
    if not phone:
        return ""
    value = re.sub(r"[^\d+]", "", phone)
    if not value:
        return ""
    if value.startswith("8"):
        value = "+7" + value[1:]
    elif not value.startswith("+"):
        value = "+" + value
    return value
