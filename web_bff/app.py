"""Первая версия web-bff: health, /me, пустая лента, POST хода в ADK.

Запуск:
  PYTHONPATH=. python -m web_bff.app

Dev-аутентификация (пока нет OTP):
  WEB_BFF_ALLOW_DEV_AUTH=true
  заголовок X-User-Id: UUID из таблицы users.id
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Optional

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from bot.services.database import AdkApiClient
from bot.services.dialog import CHANNEL_WEB, run_turn
from web_bff.auth_dev import require_dev_user
from web_bff.config import settings
from web_bff.users import get_user_by_id, profile_for_adk
from utils.logger import setup_logger

logger = setup_logger("web_bff", "web_bff.log")


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class MessageOut(BaseModel):
    message_id: str
    status: str
    blocks: list[dict[str, Any]]
    error: Optional[str] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required")
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5)
    adk = AdkApiClient(
        base_url=settings.adk_api_base,
        app_name=settings.adk_app_name,
        timeout_sec=settings.adk_timeout_sec,
    )
    await adk.open()
    app.state.pool = pool
    app.state.adk = adk
    logger.info(
        "web-bff started adk=%s app=%s dev_auth=%s",
        settings.adk_api_base,
        settings.adk_app_name,
        settings.allow_dev_auth,
    )
    try:
        yield
    finally:
        await adk.close()
        await pool.close()


app = FastAPI(title="Nastya web-bff", version="0.1.0", lifespan=lifespan)
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/me")
async def me(request: Request, user_id: str = Depends(require_dev_user)) -> dict[str, Any]:
    user = await get_user_by_id(request.app.state.pool, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found")
    if user["is_blocked"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="user_blocked")
    phone = user.get("phone_number") or ""
    masked = f"***{phone[-4:]}" if len(phone) >= 4 else None
    return {
        "id": user["id"],
        "first_name": user["first_name"],
        "last_name": user["last_name"],
        "phone_masked": masked,
    }


@app.get("/dialog")
async def get_dialog(request: Request, user_id: str = Depends(require_dev_user)) -> dict[str, Any]:
    user = await get_user_by_id(request.app.state.pool, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found")
    return {"messages": []}


@app.post("/dialog/messages", response_model=MessageOut)
async def post_message(
    request: Request,
    body: MessageIn,
    user_id: str = Depends(require_dev_user),
) -> MessageOut:
    user = await get_user_by_id(request.app.state.pool, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found")
    if user["is_blocked"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="user_blocked")

    try:
        result = await run_turn(
            request.app.state.adk,
            global_user_id=user["id"],
            text=body.text,
            channel=CHANNEL_WEB,
            profile=profile_for_adk(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception:
        logger.exception("ADK run failed user=%s", user_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="backend_unavailable",
        ) from None

    return MessageOut(
        message_id=result.message_id,
        status=result.status,
        blocks=result.blocks,
        error=result.error,
    )


def main() -> None:
    import uvicorn

    uvicorn.run(
        "web_bff.app:app",
        host=settings.host,
        port=settings.port,
        factory=False,
    )


if __name__ == "__main__":
    main()
