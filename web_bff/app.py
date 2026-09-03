"""web-bff: OTP cookie, диалог, файлы.

Запуск:
  PYTHONPATH=. python -m web_bff.app

Вход:
  POST /auth/otp/request  {"phone": "+7..."}
  POST /auth/otp/verify   {"phone": "+7...", "code": "123456"} → cookie nastya_web

Dev:
  WEB_BFF_OTP_STUB=true — код в логе и в поле dev_code
  WEB_BFF_ALLOW_DEV_AUTH=true — запасной заголовок X-User-Id
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Optional
from urllib.parse import unquote

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from bot.services.database import AdkApiClient, NewsStore, PostgresChatStore
from bot.services.dialog import CHANNEL_WEB, paginate_search, run_turn
from utils.logger import setup_logger
from web_bff.auth import clear_session_cookie, current_user, require_user, set_session_cookie
from web_bff.config import settings
from web_bff.files import FileTokenError, FileUrlIssuer, resolve_kit_file
from web_bff.news import NewsDetail, NewsListResponse, to_news_detail, to_news_list_item
from web_bff.otp import OtpError, OtpService
from web_bff.users import profile_for_adk

logger = setup_logger("web_bff", "web_bff.log")


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class MessageOut(BaseModel):
    message_id: str
    status: str
    blocks: list[dict[str, Any]]
    error: Optional[str] = None


class OtpRequestIn(BaseModel):
    phone: str = Field(min_length=5, max_length=32)


class OtpVerifyIn(BaseModel):
    phone: str = Field(min_length=5, max_length=32)
    code: str = Field(min_length=4, max_length=6)


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
    store = PostgresChatStore(settings.database_url)
    store.pool = pool
    file_urls = FileUrlIssuer(settings.file_secret)
    from utils.document_handler import DocumentHandler

    doc_handler = DocumentHandler(
        kb_manager_url=settings.kb_manager_url,
        kb_manager_token=settings.kb_manager_token,
        downloads_dir=settings.downloads_dir,
    )
    app.state.pool = pool
    app.state.store = store
    app.state.news_store = NewsStore(pool)
    app.state.adk = adk
    app.state.file_urls = file_urls
    app.state.doc_handler = doc_handler
    app.state.otp = OtpService(pool, settings)
    logger.info(
        "web-bff started adk=%s app=%s stub_otp=%s dev_auth=%s suggestions=%s",
        settings.adk_api_base,
        settings.adk_app_name,
        settings.otp_stub,
        settings.allow_dev_auth,
        settings.suggestions,
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


@app.post("/auth/otp/request")
async def otp_request(request: Request, body: OtpRequestIn) -> dict[str, Any]:
    try:
        return await request.app.state.otp.request(body.phone)
    except OtpError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@app.post("/auth/otp/verify")
async def otp_verify(request: Request, body: OtpVerifyIn) -> JSONResponse:
    try:
        token = await request.app.state.otp.verify(body.phone, body.code)
    except OtpError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    response = JSONResponse({"status": "ok"})
    set_session_cookie(response, token)
    return response


@app.post("/auth/logout")
async def logout(request: Request, response: Response) -> dict[str, str]:
    otp = getattr(request.app.state, "otp", None)
    cookie = request.cookies.get(settings.cookie_name)
    if otp is not None and cookie:
        await otp.revoke_token(cookie)
    clear_session_cookie(response)
    return {"status": "ok"}


@app.get("/news")
async def get_news(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(require_user),
) -> NewsListResponse:
    rows = await request.app.state.news_store.get_published_for_web(
        user_id,
        limit=limit,
        offset=offset,
    )
    has_more = len(rows) > limit
    items = [to_news_list_item(row) for row in rows[:limit]]
    return NewsListResponse(items=items, limit=limit, offset=offset, has_more=has_more)


@app.get("/news/{news_id}")
async def get_news_by_id(
    news_id: int,
    request: Request,
    user_id: str = Depends(require_user),
) -> NewsDetail:
    row = await request.app.state.news_store.get_published_for_web_by_id(user_id, news_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="news_not_found")
    return to_news_detail(row)


@app.get("/me")
async def me(request: Request, _user_id: str = Depends(require_user)) -> dict[str, Any]:
    user = current_user(request)
    phone = user.get("phone_number") or ""
    masked = f"***{phone[-4:]}" if len(phone) >= 4 else None
    return {
        "id": user["id"],
        "first_name": user["first_name"],
        "last_name": user["last_name"],
        "phone_masked": masked,
    }


@app.get("/dialog")
async def get_dialog(request: Request, _user_id: str = Depends(require_user)) -> dict[str, Any]:
    user = current_user(request)
    messages = await request.app.state.store.get_history(
        "0",
        global_user_id=user["id"],
        channel=CHANNEL_WEB,
    )
    for message in messages:
        message["blocks"] = refresh_file_urls(
            message.get("blocks"),
            request.app.state.file_urls,
            str(user["id"]),
        )
    return {
        "messages": messages
    }


def refresh_file_urls(
    blocks: Any,
    file_urls: FileUrlIssuer,
    user_id: str,
) -> list[dict[str, Any]] | None:
    if not isinstance(blocks, list):
        return None
    refreshed: list[dict[str, Any]] = []
    for raw_block in blocks:
        if not isinstance(raw_block, dict):
            continue
        block = dict(raw_block)
        if block.get("type") == "suggestions" and not settings.suggestions:
            continue
        if block.get("type") == "text":
            block.setdefault("format", "markdown")
        if block.get("type") == "documents" and isinstance(block.get("items"), list):
            items: list[dict[str, Any]] = []
            for raw_item in block["items"]:
                if not isinstance(raw_item, dict):
                    continue
                item = dict(raw_item)
                url = item.get("url")
                if isinstance(url, str) and url:
                    try:
                        item["url"] = file_urls.refresh_url(url, user_id)
                    except FileTokenError:
                        logger.warning("invalid stored file URL user=%s", user_id)
                        item.pop("url", None)
                items.append(item)
            block["items"] = items
        refreshed.append(block)
    return refreshed


@app.post("/dialog/messages", response_model=MessageOut)
async def post_message(
    request: Request,
    body: MessageIn,
    _user_id: str = Depends(require_user),
) -> MessageOut:
    user = current_user(request)

    try:
        result = await run_turn(
            request.app.state.adk,
            global_user_id=user["id"],
            text=body.text,
            channel=CHANNEL_WEB,
            profile=profile_for_adk(user),
            store=request.app.state.store,
            platform_user_id=0,
            file_urls=request.app.state.file_urls,
            include_suggestions=settings.suggestions,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception:
        logger.exception("ADK run failed user=%s", user["id"])
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


@app.post("/dialog/search/more", response_model=MessageOut)
async def post_search_more(
    request: Request, _user_id: str = Depends(require_user)
) -> MessageOut:
    user = current_user(request)
    result = await paginate_search(
        request.app.state.store,
        global_user_id=user["id"],
        mode="more",
        channel=CHANNEL_WEB,
        file_urls=request.app.state.file_urls,
    )
    return MessageOut(
        message_id=result.message_id,
        status=result.status,
        blocks=result.blocks,
        error=result.error,
    )


@app.post("/dialog/search/all", response_model=MessageOut)
async def post_search_all(
    request: Request, _user_id: str = Depends(require_user)
) -> MessageOut:
    user = current_user(request)
    result = await paginate_search(
        request.app.state.store,
        global_user_id=user["id"],
        mode="all",
        channel=CHANNEL_WEB,
        file_urls=request.app.state.file_urls,
    )
    return MessageOut(
        message_id=result.message_id,
        status=result.status,
        blocks=result.blocks,
        error=result.error,
    )


@app.post("/dialog/reset")
async def reset_dialog(request: Request, _user_id: str = Depends(require_user)) -> dict[str, str]:
    user = current_user(request)
    store = request.app.state.store
    await store.reset("0", user["id"], channel=CHANNEL_WEB)
    await store.reset_search_state_for_channel(user["id"], CHANNEL_WEB)
    return {"status": "ok"}


@app.get("/files/{token}")
async def get_file(
    token: str,
    request: Request,
    user_id: str = Depends(require_user),
):
    try:
        payload = request.app.state.file_urls.parse(unquote(token))
    except FileTokenError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file_not_found")
    if str(payload.get("u") or "") != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="file_forbidden")

    filename = str(payload.get("n") or "file")
    kind = payload.get("k")
    if kind == "kit":
        try:
            path = resolve_kit_file(str(payload.get("root") or ""), str(payload.get("p") or ""))
        except FileTokenError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file_not_found")
        return FileResponse(path, filename=filename, media_type="application/octet-stream")

    if kind == "kb":
        document_id = str(payload.get("id") or "")
        if not document_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file_not_found")
        file_path = await request.app.state.doc_handler.download_document(document_id)
        if file_path is None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail="file_unavailable"
            )
        cleanup = BackgroundTask(lambda p=file_path: p.unlink(missing_ok=True))
        return FileResponse(
            file_path,
            filename=filename,
            media_type="application/octet-stream",
            background=cleanup,
        )

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file_not_found")


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
