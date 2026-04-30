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
from bot.services.utils import html_to_bot, split_message
import json
from aiogram.types import BufferedInputFile
from maxapi.enums import TextFormat
import io, random
import tempfile
from maxapi.types import InputMedia

# Настройка логгера
logger = setup_logger('broadcasting', 'broadcast.log')
#  логгер событий
eventlogger = EventLogger()

def create_broadcast_app(
    news_store,
    subscriber_store,
    load_bot_start_message,
    get_start_message,
    source="telegram"
):
    app = FastAPI(title="Bot Broadcast API")
    logger.info(f"Источник для всех {source}")

    @app.post("/broadcast")
    async def broadcast(
        html: str = Form(...),
        news_id: Optional[int] = Form(None),
        files: List[UploadFile] = File(default=[]),
        schedule_time: Optional[str] = Form(None),
        reuse_file_path: Optional[str] = Form(None),
        target_group: str = Form("all"),
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
                    channel=source,
                    payload={
                        "target_group": target_group,
                        "has_files": bool(file_paths),
                        "scheduled": bool(schedule_time)
                    }
                )
                users, _ = await get_filtered_users(subscriber_store, target_group, source)    
                # СОЗДАЁМ НОВОСТЬ ТОЛЬКО ЕСЛИ ЕЁ НЕТ
                if not news_id:
                    news_id = await news_store.create_news(
                        html,
                        scheduled_at=schedule_dt,
                        files=file_paths,
                        group=target_group,
                        source="multi"
                    )
                    logger.info(f" Создана новость {news_id}")
                else:
                    logger.info(f" Используем существующую новость {news_id}")
                # news_id = await news_store.create_news(html, schedule_dt, files=file_paths, group=target_group, source=source)
                return {"status": "ok", "news_id": news_id, "sent": len(users)}
            except Exception as e:
                logger.error(f"Error while broadcast inside shecdule and news: {e}")
                await eventlogger.log_event(
                    event_type="error",
                    channel=source,
                    payload={
                        "error": str(e)
                    }
                )
                raise HTTPException(400, str(e))
        except Exception as e:
            logger.error(f"Error while broadcast all: {e}")
            await eventlogger.log_event(
                    event_type="error",
                    channel=source,
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
                    channel=source,
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
async def send_now(text: str, file_data: List, target_group: str="all", bot_holder=None, subscriber_store=None, source="telegram", news_id: Optional[int]=None):
    """Отправка новости с фильтрацией по группе"""
    sent = 0
    failed = 0
    errors = []

    users, all_count = await get_filtered_users(subscriber_store, target_group, source)
    count = len(users)

    logger.info(f"📬 Отправка новости: #{news_id} {count} из {all_count} пользователей (группа: {target_group})")        
    for idx, user_id in enumerate(users):
        try:
            if not bot_holder.instance:
                logger.warning(f"⚠️ Бот в процессе реконнекта, пропускаем user_id={user_id}")
                failed += 1
                errors.append(f"user {user_id}: bot reconnecting")
                continue

            bot = bot_holder.instance
            
            # Для MAX: peer_id должен быть int, для Telegram — можно str
            peer_id = int(user_id) if source == "max" else user_id

            # === ОТПРАВКА ТЕКСТА ===
            if text:
                parts = split_message(text)
                for part in parts:
                    try:
                        if source == "telegram":
                            await bot.send_message(peer_id, part, parse_mode="HTML")
                        else:  # max
                            await bot.send_message(user_id=peer_id, text=part, format=TextFormat.HTML)
                    except Exception as e:
                        # Fallback на plain text если HTML не прошёл
                        logger.debug(f"HTML fallback для user {user_id}: {e}")
                        await bot.send_message(user_id=peer_id, text=part)
            
            # === ОТПРАВКА ФАЙЛОВ ===
            for filename, content_type, content in file_data:
                try:
                    if source == "telegram":
                        if content_type.startswith("image"):
                            await bot.send_photo(
                                peer_id,
                                BufferedInputFile(content, filename=filename)
                            )
                        else:
                            await bot.send_document(
                                peer_id,
                                BufferedInputFile(content, filename=filename)
                            )
                    else:  # max
                        tmp_path = None
                        try:
                            tmp_path = os.path.join(tempfile.gettempdir(), filename)
                    
                            # Пишем контент в файл
                            async with aiofiles.open(tmp_path, "wb") as out:
                                await out.write(content)

                            # Отправляем через InputMedia — имя файла будет корректным
                            await bot.send_message(
                                user_id=peer_id,
                                attachments=[InputMedia(path=tmp_path)]
                            )
                            logger.info(f" Файл отправлен: {filename}")
                            
                        finally:
                            # Гарантированно удаляем временный файл
                            if tmp_path and os.path.exists(tmp_path):
                                try:
                                    os.remove(tmp_path)
                                except Exception as e:
                                    logger.warning(f" Не удалось удалить временный файл {tmp_path}: {e}")
                except Exception as e:
                    logger.warning(f"Не отправлен файл {filename} для user {user_id}: {e}")

            sent += 1
            await eventlogger.log_event(
                event_type="broadcast_sent",
                user_id=str(user_id),
                channel=source,
                payload={"news_id": news_id} if news_id else None
            )
            
            # Защита от Flood Limits (немного рандома для естественности)
            await asyncio.sleep(0.03 + random.random() * 0.04)

        except Exception as e:
            failed += 1
            errors.append(f"user {user_id}: {str(e)[:100]}")
            logger.error(f"Broadcast error to {user_id} (#{idx+1}/{count}): {e}")
            # Продолжаем отправку остальным, не прерываем цикл

    # === ИТОГОВЫЙ РЕЗУЛЬТАТ ===
    result = {
        "sent": sent,
        "failed": failed,
        "total": count,
        "success_rate": round(sent / count * 100, 1) if count > 0 else 0,
        "errors": errors[:10] if failed > 0 else []  # только первые 10 ошибок для логов
    }

    # Логирование итога
    logger.info(
        f" Новость #{news_id} ({source}): отправлено {sent}/{count}, "
        f"ошибок: {failed}, успех: {result['success_rate']}%"
    )
    return result

# получение отфильтрованных пользователей по группе для рассылки
async def get_filtered_users(subscriber_store, target_group: str = "all", source: str="telegram"):
    all_users = await subscriber_store.get_all_with_groups()

    filtered_users = []
    for user in all_users:
        user_id = user["user_id"]
        if user.get("platform") and user["platform"] != source:
            continue
        if target_group == "all":
            filtered_users.append(user_id)
        elif target_group == "manager_group" and user.get("manager_group"):
            filtered_users.append(user_id)
        elif target_group == "coach_group" and user.get("coach_group"):
            filtered_users.append(user_id)
    return filtered_users, len(all_users)
    
# планировщик для отложенных новостей 
async def news_scheduler(news_store, subscriber_store, bot_holder, source):
    logger.info("🕒 Scheduler started")
    await eventlogger.log_event(
        event_type="system_scheduler_start",
        channel=source,
        payload={
            "status": "scheduler_started"
        }
    )

    while True:
        try:
            pending = await news_store.get_pending_news_for_channel(source)
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
                    safe_html = html_to_bot(news['text'])
                    result = await send_now(
                        safe_html, 
                        file_data, 
                        target_group, 
                        bot_holder, 
                        subscriber_store, 
                        source,
                        news_id=news["id"]
                    )
                    if result["sent"] > 0:
                        await news_store.mark_channel_sent(news["id"], source)
                        await news_store.mark_sent_if_done(news["id"])

                        logger.info(
                            f"✅ Новость #{news['id']} отправлена в {source}, "
                            f"sent={result['sent']}"
                        )
                    else:
                        logger.warning(
                            f"⚠️ Новость #{news['id']} не отправлена (0 users)"
                        )
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Error during news_scheduler like this {e}")