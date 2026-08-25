"""Временная идентичность для первой версии BFF. OTP появится отдельно."""

from fastapi import Header, HTTPException, status

from web_bff.config import settings


async def require_dev_user(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> str:
    if not settings.allow_dev_auth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="dev_auth_disabled",
        )
    user_id = (x_user_id or "").strip()
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing_x_user_id",
        )
    return user_id
