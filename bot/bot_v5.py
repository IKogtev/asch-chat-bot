import asyncio
import os
from typing import Optional
import json
import html as html_module
import re
from urllib.parse import quote

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.types import FSInputFile, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from dotenv import load_dotenv
import aiohttp
import time
import tempfile
from collections import OrderedDict
from datetime import datetime, timezone

# Загружаем переменные окружения ДО импорта setup_logger
load_dotenv(override=True)

from utils import setup_logger
from utils.document_handler import DocumentHandler
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from typing import List
from pydantic import BaseModel
import uvicorn
from pathlib import Path

# Модель для запроса новостей
class BroadcastRequest(BaseModel):
    text: str

# Настройка логгера
logger = setup_logger('bot', 'bot.log')
# сохраняем пути папок в сортированном по времени словаре
CALLBACK_MAP = OrderedDict()
MAX_CALLBACK_ENTRIES = 5000
# переменные для сохранения дерева папок в кэше
TREE_CACHE = None
TREE_TS = 0
TIME_SET_WAIT = 120
PLATFORM_VERSION = os.getenv("PLATFORM_VERSION", "0.5.1")
KB_MANAGER_URL = os.getenv("KB_MANAGER_URL", "http://kb-manager:5000")
BOT_START_MESSAGE_FILE = Path("/app/data/settings/bot_start_message.md")
TITLE_START = """
👋 Привет! Я интерактивный чат-бот базы знаний компании.

📁 Выбери интересующий тебя раздел или напиши что тебя интересует сообщением.
"""
broadcast_app = FastAPI(title="Bot Broadcast API")
# отложенные новости временно храним внутри списка потом в БД будут
SCHEDULED_TASKS = []

# делаем загрузку стартового сообщения из файла если есть, иначе берем и загружаем стандартное
def load_bot_start_message():
    """Load start message from file"""
    global TITLE_START
    try:
        if BOT_START_MESSAGE_FILE.exists():
            TITLE_START = BOT_START_MESSAGE_FILE.read_text(encoding="utf-8")
            logger.info(f"Start message loaded from file: {len(TITLE_START)} symbols")
        else:
            logger.warning(f"Start file not found using standart")
    except Exception as e:
        logger.error(f"Error loading starting message: {e}")

load_bot_start_message()

class PostgresChatStore:
    """Хранилище истории диалогов в PostgreSQL"""
    
    def __init__(self, dsn: str, max_turns: int = 30):
        self.dsn = dsn
        self.max_turns = max_turns
        self.pool: Optional[asyncpg.Pool] = None
        logger.info(f"Инициализация PostgresChatStore (max_turns={max_turns})")
    
    async def connect(self) -> None:
        """Подключение к БД"""
        try:
            self.pool = await asyncpg.create_pool(self.dsn, min_size=2, max_size=10)
            logger.info("Подключение к PostgreSQL установлено")
        except Exception as e:
            logger.error(f"Ошибка подключения к БД: {e}", exc_info=True)
            raise
    
    async def ensure_schema(self) -> None:
        """Создание таблицы если не существует"""
        if not self.pool:
            logger.error("Pool не инициализирован")
            raise RuntimeError("Pool not initialized")
        
        query = """
        CREATE TABLE IF NOT EXISTS chat_history (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_user_id ON chat_history(user_id);
        CREATE INDEX IF NOT EXISTS idx_created_at ON chat_history(created_at);
        """
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(query)
            logger.info("Схема БД проверена/создана")
        except Exception as e:
            logger.error(f"Ошибка создания схемы: {e}", exc_info=True)
            raise
    
    async def append(self, user_id: int, role: str, content: str) -> None:
        """Добавление сообщения в историю"""
        if not self.pool:
            logger.warning("Pool не инициализирован, сообщение не сохранено")
            return
        
        query = """
        INSERT INTO chat_history (user_id, role, content)
        VALUES ($1, $2, $3)
        """
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(query, user_id, role, content)
            logger.debug(f"Сохранено сообщение: user={user_id}, role={role}, len={len(content)}")
        except Exception as e:
            logger.error(f"Ошибка сохранения сообщения: {e}", exc_info=True)
    
    async def get_history(self, user_id: int) -> list[dict]:
        """Получение истории диалога"""
        if not self.pool:
            logger.warning("Pool не инициализирован")
            return []
        
        query = """
        SELECT role, content, created_at
        FROM chat_history
        WHERE user_id = $1
        ORDER BY created_at DESC
        LIMIT $2
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query, user_id, self.max_turns)
            
            history = [
                {"role": row["role"], "content": row["content"]}
                for row in reversed(rows)
            ]
            logger.debug(f"Загружена история для user={user_id}: {len(history)} сообщений")
            return history
        except Exception as e:
            logger.error(f"Ошибка загрузки истории: {e}", exc_info=True)
            return []
    
    async def reset(self, user_id: int) -> None:
        """Очистка истории пользователя"""
        if not self.pool:
            logger.warning("Pool не инициализирован")
            return
        
        query = "DELETE FROM chat_history WHERE user_id = $1"
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(query, user_id)
            logger.info(f"История очищена для user={user_id}: {result}")
        except Exception as e:
            logger.error(f"Ошибка очистки истории: {e}", exc_info=True)
    
    async def close(self) -> None:
        """Закрытие пула соединений"""
        if self.pool:
            await self.pool.close()
            logger.info("Пул соединений PostgreSQL закрыт")

# Хранилище пользователей отправлявших сообщения боту 
class SubscriberStore:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
    
    async def ensure_schema(self):
        """Создание таблицы если не существует"""
        query = """
        CREATE TABLE IF NOT EXISTS subscribers (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query)

    async def add(self, user_id: int, username: str | None):
        """добавление пользователя в таблицу"""
        logger.info(f"Added a new user: {user_id}")
        query = """
        INSERT INTO subscribers (user_id, username)
        VALUES ($1, $2)
        ON CONFLICT (user_id) DO NOTHING
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query, user_id, username)

    async def get_all(self) -> list[int]:
        """Получение всех пользователей из таблицы"""
        logger.info("Получаем пользователей")
        query = "SELECT user_id FROM subscribers"
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query)
        return [r["user_id"] for r in rows]

class AdkApiClient:
    """Клиент для взаимодействия с Google ADK API"""
    
    def __init__(self, base_url: str, app_name: str):
        self.base_url = base_url.rstrip('/')
        self.app_name = app_name
        self.http: Optional[aiohttp.ClientSession] = None
        logger.info(f"Инициализация ADK клиента: {base_url}, app={app_name}")
    
    async def open(self) -> None:
        """Открытие HTTP сессии"""
        self.http = aiohttp.ClientSession()
        logger.info("HTTP сессия ADK клиента открыта")
    
    async def close(self) -> None:
        """Закрытие HTTP сессии"""
        if self.http:
            await self.http.close()
            logger.info("HTTP сессия ADK клиента закрыта")
    
    async def ensure_session(self, user_id: str, session_id: str) -> None:
        """Создание/проверка сессии в ADK"""
        if not self.http:
            logger.error("HTTP сессия не инициализирована")
            raise RuntimeError("HTTP session not initialized")
        
        url = f"{self.base_url}/apps/{self.app_name}/users/{user_id}/sessions/{session_id}"
        
        try:
            async with self.http.post(url, json={}) as resp:
                if resp.status in (200, 201):
                    logger.debug(f"Сессия создана/проверена: user={user_id}, session={session_id}")
                    return
                
                if resp.status in (400, 409):
                    try:
                        data = await resp.json()
                        detail = (data.get("detail") or "").lower()
                        if "exists" in detail or "already" in detail:
                            logger.debug(f"Сессия уже существует: user={user_id}")
                            return
                    except Exception:
                        pass
                
                text = await resp.text()
                logger.error(f"ADK ensure_session failed: {resp.status} {text}")
                raise RuntimeError(f"ADK ensure_session failed: {resp.status}")
        except aiohttp.ClientError as e:
            logger.error(f"Ошибка сети при ensure_session: {e}", exc_info=True)
            raise
    
    async def delete_session(self, user_id: str, session_id: str) -> None:
        """Удаление сессии ADK"""
        if not self.http:
            logger.error("HTTP сессия не инициализирована")
            raise RuntimeError("HTTP session not initialized")
        
        url = f"{self.base_url}/apps/{self.app_name}/users/{user_id}/sessions/{session_id}"
        
        try:
            async with self.http.delete(url) as resp:
                if resp.status in (200, 204, 404):
                    logger.info(f"🗑️ Сессия удалена для user={user_id}, session={session_id}")
                else:
                    text = await resp.text()
                    logger.warning(f"⚠️ Не удалось удалить сессию: {resp.status} - {text}")
        except aiohttp.ClientError as e:
            logger.error(f"Ошибка сети при delete_session: {e}", exc_info=True)
    
    async def run(self, user_id: str, session_id: str, text: str) -> tuple[str, list]:
        """Отправка сообщения агенту и получение ответа (БЕЗ передачи истории)"""
        if not self.http:
            logger.error("HTTP сессия не инициализирована")
            raise RuntimeError("HTTP session not initialized")
        
        url = f"{self.base_url}/run"
    
        payload = {
            "app_name": self.app_name,
            "user_id": user_id,
            "session_id": session_id,
            "new_message": {
                "role": "user",
                "parts": [{"text": text}]
            }
        }
        
        try:
            logger.debug(f"=== ADK REQUEST ===")
            logger.debug(f"URL: {url}")
            logger.debug(f"Payload: {json.dumps(payload, indent=2, ensure_ascii=False)}")
            
            async with self.http.post(url, json=payload) as resp:
                text_resp = await resp.text()
                
                logger.debug(f"=== ADK RESPONSE ===")
                logger.debug(f"Status: {resp.status}")
                logger.debug(f"Raw response: {text_resp[:1000]}")
                
                if resp.status != 200:
                    logger.error(f"ADK run failed: {resp.status} {text_resp}")
                    raise RuntimeError(f"ADK run failed: {resp.status}")
                
                try:
                    events = await resp.json()
                    logger.debug(f"=== PARSED EVENTS ===")
                    logger.debug(f"Events structure: {json.dumps(events, indent=2, ensure_ascii=False)}")
                except Exception as e:
                    logger.warning(f"Ответ не в формате JSON: {text_resp[:200]}")
                    return text_resp, []
                
                answer = self._extract_model_text(events)
                
                if not answer:
                    logger.warning("Пустой ответ от агента")
                    logger.error(f"Не удалось извлечь текст из: {json.dumps(events, indent=2, ensure_ascii=False)}")
                    return "Агент не вернул ответ", events
                
                logger.debug(f"=== EXTRACTED ANSWER ===")
                logger.debug(f"Answer: {answer}")
                return answer, events
                
        except aiohttp.ClientError as e:
            logger.error(f"Ошибка сети при run: {e}", exc_info=True)
            raise

    @staticmethod
    def _extract_model_text(events: list) -> str:
        """
        Извлечение финального текста ответа из событий ADK.

        Важно: ADK может вернуть parts вида:
          {"text": "...", "thought": True}   # внутренние рассуждения
          {"text": "..."}                   # финальный ответ пользователю

        Поэтому:
        - пропускаем part["thought"] == True
        - собираем все оставшиеся text и склеиваем
        """
        if not events:
            return ""

        out: list[str] = []

        # Проходим в прямом порядке: ответ может состоять из нескольких частей
        for event in events:
            if not isinstance(event, dict):
                continue

            # Формат 1: model_turn
            if "model_turn" in event and isinstance(event["model_turn"], dict):
                parts = event["model_turn"].get("parts", []) or []
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    if part.get("thought") is True:
                        continue
                    text = part.get("text")
                    if text:
                        out.append(text)

            # Формат 2: content (как в твоём логе: event["content"]["parts"]...)
            if "content" in event:
                content = event["content"]

                # иногда content может быть строкой
                if isinstance(content, str):
                    out.append(content)
                    continue

                if isinstance(content, dict):
                    # content.text
                    if "text" in content and isinstance(content["text"], str):
                        out.append(content["text"])

                    # content.parts
                    parts = content.get("parts", []) or []
                    for part in parts:
                        if not isinstance(part, dict):
                            continue
                        if part.get("thought") is True:
                            continue
                        text = part.get("text")
                        if text:
                            out.append(text)

            # Формат 3: прямой text (редко, но оставим)
            if "text" in event and isinstance(event["text"], str):
                out.append(event["text"])

            # Формат 4: message.content
            if "message" in event and isinstance(event["message"], dict):
                msg = event["message"]
                content = msg.get("content")

                if isinstance(content, str):
                    out.append(content)
                elif isinstance(content, dict):
                    if "text" in content and isinstance(content["text"], str):
                        out.append(content["text"])
                    parts = content.get("parts", []) or []
                    for part in parts:
                        if not isinstance(part, dict):
                            continue
                        if part.get("thought") is True:
                            continue
                        text = part.get("text")
                        if text:
                            out.append(text)

        # Склеиваем и чистим
        final = "\n".join(s.strip() for s in out if s and s.strip()).strip()
        return final
        
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
    logger.info(f"  KB Manager: {KB_MANAGER_URL}")
    logger.info(f"  Downloads: {downloads_dir}")
    logger.info(f"  Database: {dsn.split('@')[1] if '@' in dsn else 'configured'}")
    
    # Инициализация компонентов
    bot = Bot(token=tg_token)
    dp = Dispatcher()
    
    store = PostgresChatStore(dsn=dsn, max_turns=30)
    await store.connect()
    await store.ensure_schema()
    # инициализируем хранилище пользователей
    subscriber_store = SubscriberStore(store.pool)
    await subscriber_store.ensure_schema()
    
    adk = AdkApiClient(base_url=adk_base, app_name=adk_app)
    await adk.open()
    
    # Инициализация DocumentHandler
    doc_handler = DocumentHandler(
        kb_manager_url=KB_MANAGER_URL,
        kb_manager_token=kb_manager_token,
        downloads_dir=downloads_dir
    )
    
    logger.info("Все компоненты инициализированы")

    # Обработчики команд
    @dp.message(Command("start"))
    async def start(m: Message) -> None:
        user_id = m.from_user.id
        username = m.from_user.username or "unknown"

        # Добавление username в подписчиков
        await subscriber_store.add(user_id, username)

        logger.info(f"Команда /start от user_id={user_id} (@{username})")
        # строим меню для ответа
        tree = await get_tree_cached()
        menu = build_menu_from_tree(tree, [])

        await m.answer(
           TITLE_START,
           reply_markup=menu
        )
    @dp.message(Command("version"))
    async def version_info(m: Message) -> None:
        """Команда для получения версии платформы/бота"""
        user_id = m.from_user.id
        logger.info(f"Команда /version от user_id={user_id}")
        await m.answer(
            f"Текущая версия бота: {PLATFORM_VERSION}"
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
            
            await m.answer("✅ История диалога и сессия сброшены")
            logger.info(f"История и сессия сброшены для user_id={user_id}")
        except Exception as e:
            logger.error(f"Ошибка при сбросе: {e}", exc_info=True)
            await m.answer("❌ Ошибка при сбросе истории")
    
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

    def markdown_to_safe_html(text: str) -> str:
        """Конвертация Markdown в безопасный HTML для Telegram"""
        # Экранируем HTML
        text = html_module.escape(text)
        
        # **bold** -> <b>bold</b>
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
        
        # *italic* -> <i>italic</i> (только если не внутри bold)
        text = re.sub(r'(?<!</b>)\*([^*]+?)\*(?!<b>)', r'<i>\1</i>', text)
        
        # `code` -> <code>code</code>
        text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
        
        # [text](url) -> <a href="url">text</a>
        text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', text)
        
        return text

    @dp.message(F.text)
    async def on_text(m: Message) -> None:
        user_id = str(m.from_user.id)
        username = m.from_user.username or "unknown"
        
        # Добавление username в подписчиков
        await subscriber_store.add(int(user_id), username)

        session_id = "default"
        user_text = (m.text or "").strip()
        
        if not user_text:
            return
        
        logger.info(f"📨 Сообщение от user_id={user_id} (@{username}): {user_text[:100]}")
        
        try:
            # Создание/проверка сессии
            await adk.ensure_session(user_id=user_id, session_id=session_id)
            
            # Отправка в агент
            answer, events = await adk.run(
                user_id=user_id, 
                session_id=session_id, 
                text=user_text
            )
            
            logger.info(f"📤 Ответ для user_id={user_id}: {answer[:100]}")
            
            # Логируем метаданные только в DEBUG режиме
            if os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG" and events:
                try:
                    for event in events:
                        if not isinstance(event, dict):
                            continue
                        
                        # 1. Логируем usageMetadata (токены)
                        if "usageMetadata" in event:
                            usage = event["usageMetadata"]
                            logger.debug(
                                f"📊 Использование токенов: "
                                f"prompt={usage.get('promptTokenCount', 0)}, "
                                f"response={usage.get('candidatesTokenCount', 0)}, "
                                f"total={usage.get('totalTokenCount', 0)}, "
                                f"cached={usage.get('cachedContentTokenCount', 0)}"
                            )
                        
                        # 2. Логируем actions (может содержать tool info)
                        if "actions" in event and event["actions"]:
                            actions = event["actions"]
                            
                            # Проверяем stateDelta
                            if actions.get("stateDelta"):
                                logger.debug(f"🔄 State delta: {json.dumps(actions['stateDelta'], indent=2, ensure_ascii=False)}")
                            
                            # Проверяем artifactDelta
                            if actions.get("artifactDelta"):
                                logger.debug(f"📦 Artifact delta: {json.dumps(actions['artifactDelta'], indent=2, ensure_ascii=False)}")
                            
                            # Проверяем requestedToolConfirmations
                            if actions.get("requestedToolConfirmations"):
                                logger.debug(f"🔧 Tool confirmations: {json.dumps(actions['requestedToolConfirmations'], indent=2, ensure_ascii=False)}")
                        
                        # 3. Логируем author и invocationId
                        if "author" in event:
                            logger.debug(f"👤 Author: {event['author']}")
                        
                        if "invocationId" in event:
                            logger.debug(f"🆔 Invocation ID: {event['invocationId']}")
                        
                        # 4. Проверяем content.parts на tool_use/tool_response
                        if "content" in event and isinstance(event["content"], dict):
                            parts = event["content"].get("parts", [])
                            for part in parts:
                                if not isinstance(part, dict):
                                    continue
                                
                                # Если есть tool_use
                                if "tool_use" in part:
                                    logger.debug(f"🔧 Tool use: {json.dumps(part['tool_use'], indent=2, ensure_ascii=False)}")
                                
                                # Если есть tool_response
                                if "tool_response" in part:
                                    logger.debug(f"📥 Tool response: {json.dumps(part['tool_response'], indent=2, ensure_ascii=False)}")
                                
                                # Если есть function_call (альтернативный формат)
                                if "function_call" in part:
                                    logger.debug(f"🔧 Function call: {json.dumps(part['function_call'], indent=2, ensure_ascii=False)}")
                                
                                # Если есть function_response
                                if "function_response" in part:
                                    logger.debug(f"📥 Function response: {json.dumps(part['function_response'], indent=2, ensure_ascii=False)}")
                
                except Exception as log_err:
                    logger.debug(f"Не удалось извлечь метаданные из events: {log_err}")            
            
            # Сохранение в БД
            await store.append(int(user_id), "user", user_text)
            await store.append(int(user_id), "model", answer)
            
            # Извлекаем document_id из ответа
            doc_ids = doc_handler.extract_document_ids(answer)
            
            # Очищаем ответ от [document_id:...]
            clean_answer = doc_handler.remove_document_ids(answer)
            
            # Отправляем текст только если он не пустой
            if clean_answer.strip():
                html_answer = markdown_to_safe_html(clean_answer)
                await m.answer(html_answer, parse_mode="HTML")
                            
            # Если есть документы - скачиваем и отправляем
            if doc_ids:
                logger.info(f"📎 Найдено {len(doc_ids)} документов для отправки")
                
                for doc_id in doc_ids:
                    try:
                        file_path = await doc_handler.download_document(doc_id)
                        
                        if file_path and file_path.exists():
                            # Используем оригинальное имя файла
                            filename = file_path.name
                            document = FSInputFile(str(file_path), filename=filename)
                            await m.answer_document(document)
                            logger.info(f"✅ Документ '{filename}' (id: {doc_id}) отправлен user_id={user_id}")
                        else:
                            logger.warning(f"⚠️ Файл не найден для document_id: {doc_id}")
                            await m.answer(f"⚠️ Не удалось загрузить документ")
                            
                    except Exception as doc_err:
                        logger.error(f"❌ Ошибка отправки документа {doc_id}: {doc_err}", exc_info=True)
                        await m.answer(f"❌ Ошибка при загрузке документа")

                    # Удаляем временный файл после отправки
                    try:
                        if file_path and file_path.exists():
                            temp_filename = file_path.name
                            file_path.unlink()
                            logger.debug(f"🗑️ Удалён временный файл: {temp_filename}")
                    except Exception as e:
                        temp_filename = file_path.name if file_path else "unknown"
                        logger.warning(f"Не удалось удалить файл {temp_filename}: {e}")
                                                                                
        except Exception as e:
            logger.error(f"❌ Ошибка обработки сообщения от user_id={user_id}: {e}", exc_info=True)
            await m.answer(
                "😔 Произошла ошибка при обработке запроса.\n"
                "Попробуйте позже или используйте /reset для сброса диалога."
            )

    @dp.callback_query(F.data.startswith("d:"))
    async def open_dir(callback: CallbackQuery):
        """Команда обработчик открытия папки"""
        await callback.answer()
        pid = callback.data.split(":")[1]

        path = CALLBACK_MAP.get(pid)

        if path is None:
            await callback.answer("Кнопка устарела", show_alert=True)
            return
        # делаем обращение к пути снова свежим
        CALLBACK_MAP.move_to_end(pid)
        path_list = path.split("/") if path else []
        tree = await get_tree_cached()
        # строим дерево папок относительно текущей папки
        menu = build_menu_from_tree(tree, path_list)
        title = "📁 /".join(path_list) or TITLE_START
        await callback.message.edit_text(
            title,
            reply_markup=menu
        )

    @dp.callback_query(F.data.startswith("f:"))
    async def send_file(callback: CallbackQuery):
        """Обработчик отправки файлов через меню бота"""
        await callback.answer()

        pid = callback.data.split(":")[1]
        path = CALLBACK_MAP.get(pid)

        if not path:
            await callback.answer("Файл не найден", show_alert=True)
            return
        # делаем обращение к пути снова свежим
        CALLBACK_MAP.move_to_end(pid)
        doc_id = await get_document_id(path)
        if not doc_id:
            url = f"{KB_MANAGER_URL}/api/filesystem/download/?path={quote(path)}"
        else:
            url = f"{KB_MANAGER_URL}/api/documents/download/{doc_id}"
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
    
    async def send_now(text: str, file_data: List):
        sent = 0

        users = await subscriber_store.get_all()

        for user_id in users:
            try:
                # отправка текста
                if text:
                    await bot.send_message(user_id, text)

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

    async def scheduler_worker():
        logger.info("🕒 Scheduler started")

        while True:
            now = datetime.now(timezone.utc)

            for task in SCHEDULED_TASKS[:]:
                if now >= task["run_at"]:
                    logger.info(f"🚀 Выполняем отложенную задачу {task['run_at']}")
                    
                    await send_now(task['text'], task['files'])
                    SCHEDULED_TASKS.remove(task)

            await asyncio.sleep(5)
    
    @broadcast_app.post("/broadcast")
    async def broadcast(
        text: str = Form(...),
        files: List[UploadFile] = File(default=[]),
        schedule_time: Optional[str] = Form(None)
    ):
        """Функция стриминга новостей в бота"""
        file_data = []
        for f in files:
            content = await f.read()
            file_data.append((f.filename, f.content_type, content))
        if schedule_time:
            try:
                run_at = datetime.fromisoformat(schedule_time)
            except Exception:
                raise HTTPException(400, "Invalid datetime format")

            # сохраняем задачу
            SCHEDULED_TASKS.append({
                "text": text,
                "files": file_data,
                "run_at": run_at
            })

            logger.info(f"📅 Задача отложена на {run_at}")
            return {"status": "scheduled"}

        return await send_now(text, file_data)
        
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
    
    

    # Запуск бота
    try:
        logger.info("🚀 Бот запущен и готов к работе")
        # Запуск HTTP сервера в отдельной задаче
        async def run_http_server():
            config = uvicorn.Config(
                broadcast_app,
                host="0.0.0.0",
                port=8001,
                log_level="info"
            )
            server = uvicorn.Server(config)
            await server.serve()
        asyncio.create_task(scheduler_worker())
    # Запуск обоих серверов параллельно
        await asyncio.gather(
            dp.start_polling(bot),
            run_http_server()
        )
        logger.info(f"Текущая версия бота: {PLATFORM_VERSION}")
        
    finally:
        logger.info("Остановка бота...")
        await adk.close()
        await store.close()
        await bot.session.close()
        logger.info("Бот остановлен")

# функция хранения пути
def register_callback_path(path: str) -> str:
    """Регистрирует путь и возвращает его короткий ID (хэш)"""
    # path_id = str(hash(path))
    path_id = str(len(CALLBACK_MAP) + 1)
    
    # Если ID уже есть, перемещаем его в конец (он теперь "свежий")
    if path_id in CALLBACK_MAP:
        CALLBACK_MAP.move_to_end(path_id)
    
    CALLBACK_MAP[path_id] = path
    
    # Если превысили лимит, удаляем самый старый элемент (из начала)
    if len(CALLBACK_MAP) > MAX_CALLBACK_ENTRIES:
        CALLBACK_MAP.popitem(last=False)
        
    return path_id

# кэширование полученных путей 
async def get_tree_cached():
    global TREE_CACHE, TREE_TS
    # кэшируем дерево чтобы постоянно не обращаться к api 15 sec 
    if time.time() - TREE_TS < TIME_SET_WAIT:
        return TREE_CACHE

    TREE_CACHE = await get_kb_tree()
    TREE_TS = time.time()

    return TREE_CACHE

#  построить меню на основе дерева папок из kb_manager
def build_menu_from_tree(tree: dict, path: list[str]):
    """Функция для строительства деревовидной структуры папок"""
    node = tree

    for p in path:
        node = node[p]

    buttons = []

    # папки
    for key, value in node.items():
        if key == "files":
            continue
        full_path = "/".join(path + [key])
        pid = register_callback_path(full_path)

        buttons.append([
            InlineKeyboardButton(
                text=f"📁 {key}",
                callback_data=f"d:{pid}"
            )
        ])

    # файлы
    for f in node.get("files", []):
        full_path = "/".join(path + [f])

        pid = register_callback_path(full_path)
        buttons.append([
            InlineKeyboardButton(
                text=f"📄 {f}",
                callback_data=f"f:{pid}"
            )
        ])
    # навигация
    nav_buttons = []
    if path:
        parent = "/".join(path[:-1])
        pid = register_callback_path(parent)

        nav_buttons.append(
            InlineKeyboardButton(
                text="⬅ Назад",
                callback_data=f"d:{pid}"
            )
        )
        nav_buttons.append(
            InlineKeyboardButton(
                text="🏠 на главную",
                callback_data="home"
                
            )
        )
    if nav_buttons:
        buttons.append(nav_buttons)
    

    return InlineKeyboardMarkup(inline_keyboard=buttons)

# получить дерево папок
async def get_kb_tree():
    
    url = f"{KB_MANAGER_URL}/api/filesystem/folders"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.json()

# получить id документа
async def get_document_id(path: str) -> str | None:
    filename = path.split("/")[-1]

    url = f"{KB_MANAGER_URL}/api/documents"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.error(f"kb-manager error: {resp.status}")
                return None

            docs = await resp.json()

    for doc in docs:
        if doc.get("source_name") == filename:
            return doc.get("document_id")

    return None


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки (Ctrl+C)")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        raise