"""Совместимость: запасной вход по X-User-Id. Основной путь — web_bff.auth.require_user."""

from web_bff.auth import require_user as require_dev_user

__all__ = ["require_dev_user"]
