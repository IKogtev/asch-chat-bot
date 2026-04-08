"""
Синхронизация профиля пользователя из Telegram-бота в ADK.

Бот отправляет этот текст через set_user_state (POST /run, role=system).
На стороне агента сообщение всё равно может попасть в user_content — root_agent
обязан не трактовать его как запрос пользователя.
"""

BOT_USER_PROFILE_MESSAGE_PREFIX = "Контекст пользователя:"


def is_bot_user_profile_injection_message(text: str) -> bool:
    t = (text or "").lstrip()
    return t.startswith(BOT_USER_PROFILE_MESSAGE_PREFIX)
