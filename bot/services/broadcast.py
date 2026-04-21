import asyncio
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from typing import Optional, List
from pathlib import Path
import os
from datetime import datetime, timezone
from utils import setup_logger
from utils.event_logger import EventLogger
from bot.services.config import Settings
import aiofiles
from bot.services.utils import html_to_telegram, split_message
import json
from aiogram.types import BufferedInputFile

# Настройка логгера
logger = setup_logger('broadcasting', 'broadcast.log')
#  логгер событий
eventlogger = EventLogger()

def create_broadcast_app(
    news_store,
    subscriber_store,
    load_bot_start_message,
    get_start_message
):
    app = FastAPI(title="Bot Broadcast API")

    @app.post("/broadcast")
    async def broadcast(
        html: str = Form(...),
        files: List[UploadFile] = File(default=[]),
        schedule_time: Optional[str] = Form(None),
        reuse_file_path: Optional[str] = Form(None),
        target_group: str = Form("all")
    ):
        """Функция стриминга новостей в бота"""
        try:
            file_paths = []
            # Если указан reuse_file_path и файл существует, используем его, переиспользование старых новостей
            if reuse_file_path and Path(reuse_file_path).exists():
                file_paths.append({
                    "path": reuse_file_path,
                    "type": "application/octet-stream",
                    "name": Path(reuse_file_path).name
                })
                logger.info(f"Reusing file: {reuse_file_path}")

            elif files:
                for f in files:
                    content = await f.read()
                    file_path = os.path.join(Settings.UPLOAD_NEWS, f.filename)

                    async with aiofiles.open(file_path, "wb") as out:
                        await out.write(content)

                    file_paths.append({
                        "path": file_path,
                        "type": f.content_type,
                        "name": f.filename
                    })

            schedule_dt = None
            try:
                if schedule_time:
                    schedule_dt = datetime.fromisoformat(schedule_time)
                    schedule_dt = schedule_dt.astimezone(timezone.utc)
                    logger.info(f"📅 Задача отложена на {schedule_dt}")
                await eventlogger.log_event(
                    event_type="broadcast_created",
                    channel="telegram",
                    payload={
                        "target_group": target_group,
                        "has_files": bool(file_paths),
                        "scheduled": bool(schedule_time)
                    }
                )
                users, _ = await get_filtered_users(subscriber_store, target_group)    
                news_id = await news_store.create_news(html, schedule_dt, files=file_paths, group=target_group)
                return {"status": "ok", "news_send": news_id, "sent": len(users)}
            except Exception as e:
                logger.error(f"Error while broadcast inside shecdule and news: {e}")
                await eventlogger.log_event(
                    event_type="error",
                    channel="telegram",
                    payload={
                        "error": str(e)
                    }
                )
                raise HTTPException(400, str(e))
        except Exception as e:
            logger.error(f"Error while broadcast all: {e}")
            await eventlogger.log_event(
                    event_type="error",
                    channel="telegram",
                    payload={
                        "error": str(e)
                    }
                )
            raise HTTPException(400, str(e))

    @app.get("/api/news")
    async def get_news():
        """Получить все новости"""
        return await news_store.get_all()

    @app.get("/api/news/{news_id}")
    async def get_news_id(news_id: int):
        """Получить новость по ID"""
        news = await news_store.get_by_id(news_id)
        if not news:
            raise HTTPException(status_code=404, detail="News not found")
        return news

    @app.delete("/api/news/{news_id}")
    async def delete_news(news_id: int):
        """Удалить новость по ID"""
        try:
            await news_store.delete_news(news_id)
            return {"status": "ok"}
        except Exception as e:
            logger.error(f"Delete error: {e}")
            await eventlogger.log_event(
                    event_type="error",
                    channel="telegram",
                    payload={
                        "error": str(e)
                    }
                )
            raise HTTPException(500, str(e))

    @app.post("/api/reload-start-message")
    async def reload_start_message():
        """Перезагрузить стартовое сообщение из файла"""
        try:
            load_bot_start_message()
            return {"success": True, "message": "Start message reloaded", "length": len(get_start_message())}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/subscribers")
    async def get_subscribers():
        """Получить всех подписчиков с группами"""
        try:
            subscribers = await subscriber_store.get_all_with_groups()
            return subscribers
        except Exception as e:
            logger.error(f"Error getting subscribers: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/subscribers/group")
    async def update_subscriber_group(data: dict):
        """Обновить группу пользователя"""
        try:
            user_id = int(data.get("user_id"))
            group = data.get("group")
            value = bool(data.get("value"))
            if group not in Settings.AVAILABLE_GROUPS:
                raise HTTPException(status_code=400, detail="Invalid group")
            await subscriber_store.update_user_group(user_id, group, value)

            return {"status": "ok", "user_id": user_id, "group": group, "value": value}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    return app


######################################
# обработчики новостей
######################################

#  функция отправки новости с фильтрацией по группе
async def send_now(text: str, file_data: List, target_group: str="all", bot_holder=None, subscriber_store=None):
    """Отправка новости с фильтрацией по группе"""
    sent = 0

    users, all_count = await get_filtered_users(subscriber_store, target_group)
    count = len(users)

    logger.info(f"📬 Отправка новости: {count} из {all_count} пользователей (группа: {target_group})")        
    for user_id in users:
        try:
            if not bot_holder.instance:
                logger.warning("Бот сейчас в процессе реконнекта, пропускаем отправку или ждем...")
                continue
            bot = bot_holder.instance
            # отправка текста
            if text:
                # защита от слишком больших новостей, чтобы Telegram не обрезал их
                parts = split_message(text)
                for part in parts:
                    try:
                        # await bot.send_message(user_id, text)
                        await bot.send_message(user_id, part, parse_mode="HTML")
                    except Exception as e:
                        logger.error(f"HTML send error, fallback to plain: {e}")
                        await bot.send_message(user_id, part)
            
            # отправка файлов
            for filename, content_type, content in file_data:

                if content_type.startswith("image"):
                    await bot.send_photo(
                        user_id,
                        BufferedInputFile(content, filename=filename)
                    )
                else:
                    await bot.send_document(
                        user_id, 
                        BufferedInputFile(content, filename=filename)
                    )

            sent += 1
            await eventlogger.log_event(
                event_type="broadcast_sent",
                user_id=str(user_id),
                channel="telegram"
            )
            # защита от Flood Limits 
            await asyncio.sleep(0.05)

        except Exception as e:
            logger.error(f"Broadcast error to {user_id}: {e}")

    return {"sent": sent}

# получение отфильтрованных пользователей по группе для рассылки
async def get_filtered_users(subscriber_store, target_group: str = "all"):
    all_users = await subscriber_store.get_all_with_groups()

    filtered_users = []
    for user in all_users:
        user_id = user["user_id"]
        if target_group == "all":
            filtered_users.append(user_id)
        elif target_group == "manager_group" and user.get("manager_group"):
            filtered_users.append(user_id)
        elif target_group == "coach_group" and user.get("coach_group"):
            filtered_users.append(user_id)
    return filtered_users, len(all_users)
    
# планировщик для отложенных новостей 
async def news_scheduler(news_store, subscriber_store, bot_holder):
    logger.info("🕒 Scheduler started")
    await eventlogger.log_event(
        event_type="system_scheduler_start",
        channel="telegram",
        payload={
            "status": "scheduler_started"
        }
    )

    while True:
        try:
            pending = await news_store.get_pending_news()
            now = datetime.now(timezone.utc)

            for news in pending:
                if news["scheduled_at"] is None or now >= news["scheduled_at"]:
                    logger.info(f"🚀 Выполняем отложенную задачу {news['scheduled_at']}")
                    files = json.loads(news.get("files") or "[]")
                    file_data = []
                    for f in files:
                        try: 
                            with open(f["path"], "rb") as fp:
                                content = fp.read()
                                file_data.append((f["name"], f["type"], content))
                        except Exception as e:
                            logger.error(f"File read error: {e}")
                    target_group = news.get("target_group", "all")
                    safe_html = html_to_telegram(news['text'])
                    await send_now(safe_html, file_data, target_group, bot_holder, subscriber_store)
                    await news_store.mark_sent(news["id"])

            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Error during news_scheduler like this {e}")