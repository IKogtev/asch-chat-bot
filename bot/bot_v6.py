import asyncio
import os
from typing import Optional, Any
import json
import re
from urllib.parse import quote

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, FSInputFile, BufferedInputFile, CallbackQuery, ReplyKeyboardRemove
    )
from aiogram.exceptions import TelegramNetworkError
from aiogram.client.session.aiohttp import AiohttpSession

from dotenv import load_dotenv
import aiohttp
import time
import tempfile
from datetime import datetime, timezone
from typing import List
import uvicorn
from pathlib import Path

# Загружаем переменные окружения ДО импорта setup_logger
load_dotenv(override=True)

from utils import setup_logger
from utils.document_handler import DocumentHandler
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
#  импортируем вынесенные классы для работы с базами данных
from bot.database import PostgresChatStore, SubscriberStore, NewsStore, AdkApiClient
# импортируем конфиг
from bot.config import Settings
#  импортируем функции вспомогательные для бота
from bot.utils import (
    markdown_to_safe_html, parse_download_ranks, render_results, extract_bot_contract, 
    normalize_contract_results, extract_search_results_from_events, handle_download_by_ranks,
    handle_show_all, handle_show_more, split_message, html_to_telegram, build_menu_from_tree,
    get_kb_tree, get_document_id)

##################################
# Глобальные константы и переменные
##################################

# Настройка логгера
logger = setup_logger('bot', 'bot.log')
# создаем папку для загрузки новостей, если ее нет
Settings.UPLOAD_NEWS.mkdir(parents=True, exist_ok=True)
# стартовое сообщение
TITLE_START = """
👋 Привет! Я интерактивный чат-бот базы знаний компании.

📁 Выбери интересующий тебя раздел или напиши что тебя интересует сообщением.
"""
# переменные для сохранения дерева папок в кэше
TREE_CACHE = None
TREE_TS = 0
# создаем FastAPI для получения запросов на рассылку от внешних систем (админки)
broadcast_app = FastAPI(title="Bot Broadcast API")

#####################################
# Главная функция и обработчики бота
#####################################        
async def main() -> None:
    """Главная функция бота"""
    logger.info("=" * 60)
    logger.info("Запуск Telegram бота")
    logger.info("=" * 60)
    # Загрузка конфигурации
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not tg_token:
        logger.error("TELEGRAM_BOT_TOKEN отсутствует в .env")
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in .env")
    
    dsn = (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_DSN") or "").strip()
    if not dsn:
        logger.error("DATABASE_URL отсутствует в .env")
        raise RuntimeError("DATABASE_URL (or POSTGRES_DSN) is missing in .env")
    
    adk_base = os.getenv("ADK_API_BASE", "http://agent:8000").strip()
    adk_app = os.getenv("ADK_APP_NAME", "agent").strip()
    
    # Конфигурация для DocumentHandler
    kb_manager_token = os.getenv("KB_MANAGER_TOKEN", "").strip() or None
    downloads_dir = os.getenv("DOWNLOADS_DIR", "./downloads").strip()
    
    logger.info(f"Конфигурация:")
    logger.info(f"  ADK Base: {adk_base}")
    logger.info(f"  ADK App: {adk_app}")
    logger.info(f"  KB Manager: {Settings.KB_MANAGER_URL}")
    logger.info(f"  Downloads: {downloads_dir}")
    logger.info(f"  Database: {dsn.split('@')[1] if '@' in dsn else 'configured'}")
    # ссылка на прокси для телеграма (если нужна)
    proxy_url = os.getenv("TELEGRAM_PROXY")
    store = PostgresChatStore(dsn=dsn, max_turns=30)
    await store.connect()
    await store.ensure_schema()
    # инициализируем хранилище пользователей
    subscriber_store = SubscriberStore(store.pool)
    await subscriber_store.ensure_schema()
    # инициализируем хранилище новостей
    news_store = NewsStore(store.pool)
    await news_store.ensure_schema()
    
    adk = AdkApiClient(base_url=adk_base, app_name=adk_app)
    await adk.open()
    
    # Инициализация DocumentHandler
    doc_handler = DocumentHandler(
        kb_manager_url=Settings.KB_MANAGER_URL,
        kb_manager_token=kb_manager_token,
        downloads_dir=downloads_dir
    )
    
    logger.info("Все компоненты инициализированы")
            
    @broadcast_app.post("/broadcast")
    async def broadcast(
        html: str = Form(...),
        files: List[UploadFile] = File(default=[]),
        schedule_time: Optional[str] = Form(None),
        reuse_file_path: Optional[str] = Form(None),
        target_group: str = Form("all")
    ):
        """Функция стриминга новостей в бота"""
        try: 
            safe_html = html_to_telegram(html)
            file_paths = []
            if reuse_file_path and Path(reuse_file_path).exists():
                file_path = reuse_file_path
                file_paths.append({
                    "path": file_path,
                    "type": "application/octet-stream",
                    "name": Path(file_path).name
                })
                logger.info(f"Reusing file: {file_path}")
            
            elif files:
                for f in files:
                    content = await f.read()
                    file_path = os.path.join(Settings.UPLOAD_NEWS, f"{f.filename}")
                    
                    with open(file_path, "wb") as out:
                        out.write(content)

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
                users, _ = await get_filtered_users(target_group)    
                news_id = await news_store.create_news(html, schedule_dt, files=file_paths, group=target_group)
                return {"status": "ok", "news_send": news_id, "sent": len(users)}
            except Exception as e:
                logger.error(f"Error while broadcast inside shecdule and news: {e}")
                raise HTTPException(400, str(e))
        except Exception as e:
            logger.error(f"Error while broadcast all: {e}")
            raise HTTPException(400, str(e))

    @broadcast_app.get("/api/news")
    async def get_news():
        """Получить все новости"""
        return await news_store.get_all()

    @broadcast_app.get("/api/news/{news_id}")
    async def get_news_id(news_id: int):
        """Получить новость по ID"""
        news = await news_store.get_by_id(news_id)
        if not news:
            raise HTTPException(status_code=404, detail="News not found")
        return news
    
    @broadcast_app.delete("/api/news/{news_id}")
    async def delete_news(news_id: int):
        try: 
            await news_store.delete_news(news_id)
            return {"status": "ok"}
        except Exception as e:
            logger.error(f"Delete news error: {e}")
            raise HTTPException(500, str(e))

    @broadcast_app.post("/api/reload-start-message")
    async def reload_start_message():
        """Перезагрузить стартовое сообщение из файла"""
        try:
            load_bot_start_message()
            return {
                "success": True,
                "message": "Start message reloaded",
                "length": len(TITLE_START)
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @broadcast_app.get("/api/subscribers")
    async def get_subscribers():
        """Получить всех подписчиков с группами"""
        try:
            subscribers = await subscriber_store.get_all_with_groups()
            return subscribers
        except Exception as e:
            logger.error(f"Error getting subscribers: {e}")
            raise HTTPException(500, str(e))

    @broadcast_app.post("/api/subscribers/group")
    async def update_subscriber_group(data: dict):
        """Обновить группу пользователя"""
        try:
            user_id = int(data.get("user_id"))
            group = data.get("group")
            value = bool(data.get("value"))
            
            if group not in ("manager_group", "coach_group"):
                raise HTTPException(400, "Invalid group")
            
            await subscriber_store.update_user_group(user_id, group, value)
            
            return {"status": "ok", "user_id": user_id, "group": group, "value": value}
        except Exception as e:
            raise HTTPException(400, str(e))
            
    # получение отфильтрованных пользователей по группе для рассылки
    async def get_filtered_users(target_group: str = "all"):
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

    #  функция отправки новости с фильтрацией по группе
    async def send_now(text: str, file_data: List, target_group: str="all"):
        """Отправка новости с фильтрацией по группе"""
        sent = 0

        users, all_count = await get_filtered_users(target_group)
        count = len(users)

        logger.info(f"📬 Отправка новости: {count} из {all_count} пользователей (группа: {target_group})")        
        
        for user_id in users:
            try:
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
                # защита от Flood Limits 
                await asyncio.sleep(0.05)

            except Exception as e:
                logger.error(f"Broadcast error to {user_id}: {e}")

        return {"sent": sent}

    # планировщик для отложенных новостей 
    async def news_scheduler():
        logger.info("🕒 Scheduler started")

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
                        await send_now(safe_html, file_data, target_group)
                        await news_store.mark_sent(news["id"])

                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Error during news_scheduler like this {e}")

    http_task = asyncio.create_task(run_http_server())
    scheduler_task = asyncio.create_task(news_scheduler())
    logger.info("🚀 HTTP сервер и scheduler запущены")
    # Запуск бота
    try:
        logger.info("🚀 Бот запущен и готов к работе")
        while True:
            bot_instance = None
            try:
                logger.info("Попытка подключения к Telegram API...")

                if proxy_url:
                    session = AiohttpSession(proxy=proxy_url)
                    bot_instance = Bot(token=tg_token, session=session)
                    logger.info(f"Используется Telegram proxy: {proxy_url}")
                else:
                    bot_instance = Bot(token=tg_token)

                me = await bot_instance.get_me()
                logger.info(f"✅ Успешное подключение к Telegram API. Бот: @{me.username}")
                
                # Инициализация компонентов
                bot = bot_instance
                dp = Dispatcher()
                # регистрация обработчиков сообщений и команд бота
                register_handlers(
                    dp=dp,
                    store=store,
                    subscriber_store=subscriber_store,
                    news_store=news_store,
                    adk=adk,
                    doc_handler=doc_handler
                )
                logger.info("✓ Диспетчер инициализирован")

                logger.info(f"🚀 Бот запущен и готов к работе (версия {Settings.PLATFORM_VERSION})")
                await dp.start_polling(bot_instance)
            except TelegramNetworkError as e:
                logger.warning(
                    f"⚠️ Нет доступа к Telegram API: {e}. "
                    f"Следующая попытка через {Settings.RECONNECT_DELAY_SEC} секунд."
                )
                await asyncio.sleep(Settings.RECONNECT_DELAY_SEC)

            except (
                aiohttp.ClientError,
                OSError,
                ConnectionResetError,
                TimeoutError,
            ) as e:
                logger.warning(
                    f"⚠️ Сетевая ошибка при работе с Telegram API: {e}. "
                    f"Следующая попытка через {Settings.RECONNECT_DELAY_SEC} секунд."
                )
                await asyncio.sleep(Settings.RECONNECT_DELAY_SEC)

            except asyncio.CancelledError:
                logger.info("Получен сигнал остановки бота")
                break

            except Exception as e:
                logger.error(f"❌ Ошибка при работе бота: {e}", exc_info=True)
                logger.info(f"Перезапуск через {Settings.RECONNECT_DELAY_SEC} секунд...")
                await asyncio.sleep(Settings.RECONNECT_DELAY_SEC)

            finally:
                if bot_instance and hasattr(bot_instance, "session") and bot_instance.session:
                    try:
                        await bot_instance.session.close()
                    except Exception as e:
                        logger.warning(f"Ошибка при закрытии сессии бота: {e}")
        
    except KeyboardInterrupt:
        logger.info("Получен сигнал прерывания (Ctrl+C)")

    finally:
        logger.info("Остановка фоновых задач...")
        for task, name in [(http_task, "HTTP сервер"), (scheduler_task, "Scheduler")]:
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    logger.info(f"✓ {name} остановлен")
        try:
            await adk.close()
            logger.info("✅ ADK клиент закрыт")
        except Exception as e:
            logger.error(f"Ошибка при закрытии ADK: {e}")
        try:
            await store.close()
            logger.info("✅ Подключение к базе закрыто")
        except Exception as e:
            logger.error(f"Ошибка при закрытии БД: {e}")
        try:
            await bot.session.close()
            logger.info("✅ Сессия бота закрыта")
        except Exception as e:
            logger.error(f"Ошибка при закрытии сессии бота: {e}")
        logger.info("Бот остановлен")
    

######################################
# обработчики сообщений и команд бота
######################################
def register_handlers(dp: Dispatcher, store, subscriber_store, news_store, adk, doc_handler) -> None:
    """Регистрация всех обработчиков сообщений"""
    # Обработчик команды /start
    @dp.message(Command("start"))
    async def start(m: Message) -> None:
        user_id = m.from_user.id
        username = m.from_user.username or "unknown"
        first_name = m.from_user.first_name
        last_name = m.from_user.last_name
        last_seen = datetime.now()
        existing_phone = await subscriber_store.get_phone(user_id)
        # Добавление в подписчиков
        await subscriber_store.add(
            user_id=user_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            last_seen=last_seen,
            phone_number=None
        )
        if existing_phone:
            # Если телефон есть - загружаем данные в ADK
            session_id = f"session-{user_id}"
            await adk.ensure_session(user_id=str(user_id), session_id=session_id)
            
            user_data = await subscriber_store.get_user_data(user_id)
            if user_data:
                await adk.set_user_state(str(user_id), session_id, user_data)
                logger.info(f"📋 Данные пользователя загружены в ADK: {user_data.get('phone_number')}")
        
            logger.info(f"Команда /start от user_id={user_id} (@{username}) - телефон уже есть: {existing_phone}")
            # строим меню для ответа
            tree = await get_tree_cached()
            menu = build_menu_from_tree(tree, [])

            await m.answer(
            TITLE_START,
            reply_markup=menu
            )
        else: 
            logger.info(f"Команда /start от user_id={user_id} (@{username}) — запрашиваем телефон")
            # Запрашиваем телефон
            await m.answer(
                f"👋 Привет, {first_name}!\n\n"
                f"Для связи с вами нам нужен ваш номер телефона.\n\n"
                f"Пожалуйста, нажмите кнопку ниже чтобы поделиться номером.\n"
                f"Это нужно только для уведомлений о важных обновлениях.",
                reply_markup=Settings.PHONE_KEYBOARD
            )

    # обработчик получения контакта (номера телефона)
    @dp.message(F.contact)
    async def handle_contact(m: Message) -> None:
        """Обработка получения номера телефона"""
        if not m.contact:
            return
        
        user_id = m.from_user.id
        # Проверяем, что пользователь отправил свой контакт
        if m.contact.user_id != user_id:
            await m.answer("⚠️ Пожалуйста, отправьте свой номер телефона")
            return
        
        phone = m.contact.phone_number
        
        # Сохраняем телефон в БД
        await subscriber_store.update_phone(user_id, phone)
        
        logger.info(f"✓ Получен телефон от user_id={user_id}.")
        # Создаём сессию и загружаем данные в ADK
        session_id = f"session-{user_id}"
        await adk.ensure_session(user_id=str(user_id), session_id=session_id)
        
        user_data = await subscriber_store.get_user_data(user_id)
        if user_data:
            await adk.set_user_state(str(user_id), session_id, user_data)
        
        # Убираем клавиатуру и показываем меню
        tree = await get_tree_cached()
        menu = build_menu_from_tree(tree, [])
        
        await m.answer(
            "✅ Спасибо! Теперь вы можете пользоваться ботом.",
            reply_markup=ReplyKeyboardRemove()
        )
        await m.answer(TITLE_START, reply_markup=menu)

    # обработчик команды /version для получения версии
    @dp.message(Command("version"))
    async def version_info(m: Message) -> None:
        """Команда для получения версии платформы/бота"""
        user_id = m.from_user.id
        logger.info(f"Команда /version от user_id={user_id}")
        await m.answer(
            f"Текущая версия бота: {Settings.PLATFORM_VERSION}"
        )

    #домашняя страница
    @dp.callback_query(lambda c: c.data == "home")
    async def go_home(callback: CallbackQuery):
        # обработчик перехода на главную страницу кнопка home
        await callback.answer()
        # строим меню для ответа
        tree = await get_tree_cached()
        menu = build_menu_from_tree(tree, [])

        await callback.message.edit_text(
            TITLE_START,
            reply_markup=menu
        )

    # обработчик команды /reset для сброса истории и сессии
    @dp.message(Command("reset"))
    async def reset(m: Message) -> None:
        user_id = m.from_user.id
        username = m.from_user.username or "unknown"
        session_id = "default"
        
        logger.info(f"Команда /reset от user_id={user_id} (@{username})")
        
        try:
            # Удаляем сессию в ADK
            await adk.delete_session(user_id=str(user_id), session_id=session_id)
            
            # Создаём новую сессию
            await adk.ensure_session(user_id=str(user_id), session_id=session_id)
            
            # Очищаем историю в БД
            await store.reset(user_id)
            
            # Удаляем состояние результатов поиска
            await store.reset_search_state(user_id, session_id)
            
            # после reset сразу вернуть профиль в state
            user_data = await subscriber_store.get_user_data(user_id)
            if user_data:
                await adk.set_user_state(str(user_id), session_id, user_data)
                
            await m.answer("✅ История диалога и сессия сброшены")
            logger.info(f"История и сессия сброшены для user_id={user_id}")
        except Exception as e:
            logger.error(f"Ошибка при сбросе: {e}", exc_info=True)
            await m.answer("❌ Ошибка при сбросе истории")
    
    #  обработчик команды /help для отображения справки
    @dp.message(Command("help"))
    async def help_cmd(m: Message) -> None:
        user_id = m.from_user.id
        logger.info(f"Команда /help от user_id={user_id}")
        
        await m.answer(
            "ℹ️ Я помогу найти информацию в базе знаний.\n\n"
            "Просто напиши свой вопрос, и я постараюсь найти ответ!\n\n"
            "Команды:\n"
            "/start — начать работу\n"
            "/reset — сбросить историю\n"
            "/help — эта справка"
        )
    
    # обработчик всех текстовых сообщений (основной диалог)
    @dp.message(F.text)
    async def on_text(m: Message) -> None:
        user_id = int(m.from_user.id)
        username = m.from_user.username or "unknown"
        first_name = m.from_user.first_name
        last_name = m.from_user.last_name
        last_seen = datetime.now()
        existing_phone = await subscriber_store.get_phone(int(user_id))
        # Добавление в подписчиков
        await subscriber_store.add(
            user_id=int(user_id),
            username=username,
            first_name=first_name,
            last_name=last_name,
            last_seen=last_seen,
            phone_number=None
        )
        if not existing_phone: 
            logger.info(f"Команда /start от user_id={user_id} (@{username}) — запрашиваем телефон")
            # Запрашиваем телефон
            await m.answer(
                f"👋 Привет, {first_name}!\n\n"
                f"Для связи с вами нам нужен ваш номер телефона.\n\n"
                f"Пожалуйста, нажмите кнопку ниже чтобы поделиться номером.\n"
                f"Это нужно только для уведомлений о важных обновлениях.",
                reply_markup=Settings.PHONE_KEYBOARD
            )
        else: 

            session_id = f"session-{user_id}"
            user_text = (m.text or "").strip()

            if not user_text:
                return

            logger.info(f"📨 Сообщение от user_id={user_id} (@{username}): {user_text[:100]}")

            try:
                # 1. follow-up: download by rank
                ranks = parse_download_ranks(user_text)
                if ranks:
                    handled = await handle_download_by_ranks(
                        m=m,
                        store=store,
                        doc_handler=doc_handler,
                        user_id=user_id,
                        session_id=session_id,
                        ranks=ranks,
                    )
                    if handled:
                        return
                
                # 3. follow-up: show more
                if Settings.SHOW_MORE_RE.match(user_text) and Settings.SHOW_BY_PAGE:
                    handled = await handle_show_more(
                        m=m,
                        store=store,
                        user_id=user_id,
                        session_id=session_id,
                        page_size=Settings.SHOW_MAX,
                    )
                    if handled:
                        return

                # 2. follow-up: show all
                if Settings.SHOW_ALL_RE.match(user_text):
                    handled = await handle_show_all(
                        m=m,
                        store=store,
                        user_id=user_id,
                        session_id=session_id,
                    )
                    if handled:
                        return

                # 4. обычный запрос -> ADK
                await adk.ensure_session(user_id=str(user_id), session_id=session_id)

                # Загружаем данные пользователя в состояние ADK (на случай если сессия новая)
                user_data = await subscriber_store.get_user_data(int(user_id))
                if user_data:
                    await adk.set_user_state(user_id, session_id, user_data)
        
                answer, events = await adk.run(
                    user_id=str(user_id),
                    session_id=session_id,
                    text=user_text
                )

                logger.info(f"📤 Ответ для user_id={user_id}: {answer[:100]}")

                # DEBUG-логирование оставляем
                if os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG" and events:
                    try:
                        for event in events:
                            if not isinstance(event, dict):
                                logger.debug("Event not a dict")
                                continue
                            if "usageMetadata" in event:
                                usage = event["usageMetadata"]
                                logger.debug(
                                    f"📊 Использование токенов: "
                                    f"prompt={usage.get('promptTokenCount', 0)}, "
                                    f"response={usage.get('candidatesTokenCount', 0)}, "
                                    f"total={usage.get('totalTokenCount', 0)}, "
                                    f"cached={usage.get('cachedContentTokenCount', 0)}"
                                )

                            if "actions" in event and event["actions"]:
                                actions = event["actions"]

                                if actions.get("stateDelta"):
                                    logger.debug(f"🔄 State delta: {json.dumps(actions['stateDelta'], indent=2, ensure_ascii=False)}")

                                if actions.get("artifactDelta"):
                                    logger.debug(f"📦 Artifact delta: {json.dumps(actions['artifactDelta'], indent=2, ensure_ascii=False)}")

                                if actions.get("requestedToolConfirmations"):
                                    logger.debug(f"🔧 Tool confirmations: {json.dumps(actions['requestedToolConfirmations'], indent=2, ensure_ascii=False)}")

                            if "author" in event:
                                logger.debug(f"👤 Author: {event['author']}")

                            if "invocationId" in event:
                                logger.debug(f"🆔 Invocation ID: {event['invocationId']}")

                            if "content" in event and isinstance(event["content"], dict):
                                parts = event["content"].get("parts", [])
                                for part in parts:
                                    if not isinstance(part, dict):
                                        continue

                                    if "tool_use" in part:
                                        logger.debug(f"🔧 Tool use: {json.dumps(part['tool_use'], indent=2, ensure_ascii=False)}")

                                    if "tool_response" in part:
                                        logger.debug(f"📥 Tool response: {json.dumps(part['tool_response'], indent=2, ensure_ascii=False)}")

                                    if "function_call" in part:
                                        logger.debug(f"🔧 Function call: {json.dumps(part['function_call'], indent=2, ensure_ascii=False)}")

                                    if "function_response" in part:
                                        logger.debug(f"📥 Function response: {json.dumps(part['function_response'], indent=2, ensure_ascii=False)}")

                    except Exception as log_err:
                        logger.debug(f"Не удалось извлечь метаданные из events: {log_err}")

                # 5. сохраняем историю диалога
                await store.append(user_id, "user", user_text)
                await store.append(user_id, "model", answer)

                # 6. пробуем сначала вытащить bot_contract из ответа агента
                contract = extract_bot_contract(answer)
                if contract:
                    reranked_items = normalize_contract_results(contract)

                    await store.save_search_results(
                        user_id=user_id,
                        session_id=session_id,
                        query=user_text,
                        items=reranked_items,
                        shown_count=min(Settings.SHOW_MAX, len(reranked_items)),
                    )
                    logger.info(f"💾 Сохранён search-state из bot_contract: {len(reranked_items)} документов для user_id={user_id}")

                    if reranked_items:
                        top_items = reranked_items[:Settings.SHOW_MAX]
                        text = render_results(top_items, total=len(reranked_items), offset=0)
                        await m.answer(text, parse_mode="HTML")
                    else:
                        await m.answer("Не нашёл релевантных файлов по запросу.")
                    return
                
                # 7. fallback: старая логика через events
                extracted_items = extract_search_results_from_events(events)
                if extracted_items:
                    await store.save_search_results(
                        user_id=user_id,
                        session_id=session_id,
                        query=user_text,
                        items=extracted_items,
                        shown_count=min(8, len(extracted_items)),
                    )
                    logger.info(f"💾 Сохранён search-state: {len(extracted_items)} документов для user_id={user_id}")
                else:
                    logger.info("ℹ️ Из events не удалось извлечь search-state")

                # 8. answer пользователю — старый путь
                clean_answer = doc_handler.remove_document_ids(answer)
                if clean_answer.strip():
                    html_answer = markdown_to_safe_html(clean_answer)
                    await m.answer(answer, parse_mode="HTML") 

            except Exception as e:
                logger.error(f"❌ Ошибка обработки сообщения от user_id={user_id}: {e}", exc_info=True)
                await m.answer(
                    "😔 Произошла ошибка при обработке запроса.\n"
                    "Попробуйте позже или используйте /reset для сброса диалога."
                )

    #  обработчик открытия папки в меню бота
    @dp.callback_query(F.data.startswith("d:"))
    async def open_dir(callback: CallbackQuery):
        """Команда обработчик открытия папки"""
        await callback.answer()
        pid = callback.data.split(":")[1]

        path = Settings.CALLBACK_MAP.get(pid)

        if path is None:
            await callback.answer("Кнопка устарела", show_alert=True)
            return
        # делаем обращение к пути снова свежим
        Settings.CALLBACK_MAP.move_to_end(pid)
        path_list = path.split("/") if path else []
        tree = await get_tree_cached()
        # строим дерево папок относительно текущей папки
        menu = build_menu_from_tree(tree, path_list)
        title = "📁 /".join(path_list) or TITLE_START
        await callback.message.edit_text(
            title,
            reply_markup=menu
        )

    # обработчик открытия файла в меню бота
    @dp.callback_query(F.data.startswith("f:"))
    async def send_file(callback: CallbackQuery):
        """Обработчик отправки файлов через меню бота"""
        await callback.answer()

        pid = callback.data.split(":")[1]
        path = Settings.CALLBACK_MAP.get(pid)

        if not path:
            await callback.answer("Файл не найден", show_alert=True)
            return
        # делаем обращение к пути снова свежим
        Settings.CALLBACK_MAP.move_to_end(pid)
        doc_id = await get_document_id(path)
        if not doc_id:
            url = f"{Settings.KB_MANAGER_URL}/api/filesystem/download/?path={quote(path)}"
        else:
            url = f"{Settings.KB_MANAGER_URL}/api/documents/download/{doc_id}"
        filename = path.split("/")[-1]

        # скачиваем файл
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    await callback.answer("Ошибка загрузки файла", show_alert=True)
                    return

                tmp = tempfile.NamedTemporaryFile(delete=False)
                tmp.write(await resp.read())
                tmp.close()

        # отправляем
        await callback.message.answer_document(
            document=FSInputFile(tmp.name, filename=filename),
            # caption=filename
        )

        os.remove(tmp.name)
    

##################################
# Вспомогательные функции
##################################
# делаем загрузку стартового сообщения из файла если есть, иначе берем и загружаем стандартное
def load_bot_start_message():
    """Load start message from file"""
    global TITLE_START
    try:
        if Settings.BOT_START_MESSAGE_FILE.exists():
            TITLE_START = Settings.BOT_START_MESSAGE_FILE.read_text(encoding="utf-8")
            logger.info(f"Start message loaded from file: {len(TITLE_START)} symbols")
        else:
            logger.warning(f"Start file not found using standart")
    except Exception as e:
        logger.error(f"Error loading starting message: {e}")
# загрузка стартового сообщения
load_bot_start_message()

# Запуск HTTP сервера в отдельной задаче
async def run_http_server():
    """Запуск HTTP сервера"""
    try:
        config = uvicorn.Config(
            broadcast_app,
            host="0.0.0.0",
            port=8001,
            log_level="info"
        )
        server = uvicorn.Server(config)
        await server.serve()
    except asyncio.CancelledError:
        logger.info("HTTP сервер остановлен")
        
# кэширование полученных путей 
async def get_tree_cached():
    global TREE_CACHE, TREE_TS
    # кэшируем дерево чтобы постоянно не обращаться к api 15 sec 
    if TREE_CACHE and time.time() - TREE_TS < Settings.TIME_SET_WAIT:
        return TREE_CACHE

    TREE_CACHE = await get_kb_tree()
    TREE_TS = time.time()

    return TREE_CACHE

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки (Ctrl+C)")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        raise