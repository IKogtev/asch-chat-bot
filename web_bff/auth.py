"""Идентичность web-bff: cookie-сессия, запасной X-User-Id при WEB_BFF_ALLOW_DEV_AUTH."""

from __future__ import annotations

from typing import Any

from fastapi import Header, HTTPException, Request, Response, status

from web_bff.config import settings
from web_bff.users import get_user_by_id


async def require_user(
    request: Request,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> str:
    user_id = await _resolve_user_id(request, x_user_id)
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="backend_unavailable",
        )
    user = await get_user_by_id(pool, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
        )
    if user["is_blocked"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="user_blocked",
        )
    request.state.user = user
    return user_id


async def _resolve_user_id(request: Request, x_user_id: str | None) -> str:
    otp = getattr(request.app.state, "otp", None)
    cookie = request.cookies.get(settings.cookie_name)
    if otp is not None and cookie:
        user_id = await otp.user_id_for_token(cookie)
        if user_id:
            return user_id

    if settings.allow_dev_auth:
        header_id = (x_user_id or "").strip()
        if header_id:
            return header_id

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="unauthorized",
    )


def current_user(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
        )
    return user


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.session_ttl_sec,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=settings.cookie_name, path="/")
