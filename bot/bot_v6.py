import asyncio
import os
from typing import Optional, Any
import json
import html as html_module
import re
from uuid import uuid4
from urllib.parse import quote

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.types import (
    Message, FSInputFile, BufferedInputFile, InlineKeyboardMarkup, 
    InlineKeyboardButton, CallbackQuery, KeyboardButton, ReplyKeyboardMarkup,
    ReplyKeyboardRemove
    )
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
SHOW_MAX = int(os.getenv("SHOW_LIST_SIZE",5))
SHOW_BY_PAGE = bool(os.getenv("SHOW_BY_PAGE",False))
UPLOAD_NEWS = Path("/app/data/upload")
UPLOAD_NEWS.mkdir(parents=True, exist_ok=True)

TITLE_START = """
👋 Привет! Я интерактивный чат-бот базы знаний компании.

📁 Выбери интересующий тебя раздел или напиши что тебя интересует сообщением.
"""
broadcast_app = FastAPI(title="Bot Broadcast API")

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
        """Создание таблиц если не существуют"""
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

        CREATE TABLE IF NOT EXISTS search_meta (
            user_id BIGINT NOT NULL,
            session_id TEXT NOT NULL,
            search_id TEXT NOT NULL,
            query TEXT NOT NULL,
            total_count INT NOT NULL,
            shown_count INT NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (user_id, session_id)
        );

        CREATE TABLE IF NOT EXISTS search_results (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            session_id TEXT NOT NULL,
            search_id TEXT NOT NULL,
            rank INT NOT NULL,
            document_id TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_path TEXT,
            score DOUBLE PRECISION,
            snippet TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_search_results_user_session
        ON search_results(user_id, session_id, rank);

        CREATE INDEX IF NOT EXISTS idx_search_results_search_id
        ON search_results(search_id);
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
    
    async def save_search_results(
        self,
        user_id: int,
        session_id: str,
        query: str,
        items: list[dict],
        shown_count: int = SHOW_MAX,
    ) -> str:
        if not self.pool:
            raise RuntimeError("Pool not initialized")

        search_id = str(uuid4())
        shown_count = min(shown_count, len(items))

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM search_results WHERE user_id = $1 AND session_id = $2",
                    user_id, session_id
                )
                await conn.execute(
                    "DELETE FROM search_meta WHERE user_id = $1 AND session_id = $2",
                    user_id, session_id
                )

                await conn.execute(
                    """
                    INSERT INTO search_meta (user_id, session_id, search_id, query, total_count, shown_count)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    user_id, session_id, search_id, query, len(items), shown_count
                )

                for item in items:
                    await conn.execute(
                        """
                        INSERT INTO search_results
                        (user_id, session_id, search_id, rank, document_id, source_name, source_path, score, snippet)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                        """,
                        user_id,
                        session_id,
                        search_id,
                        item["rank"],
                        item["document_id"],
                        item["source_name"],
                        item.get("source_path"),
                        item.get("score"),
                        item.get("snippet"),
                    )

        return search_id


    async def get_last_search_meta(self, user_id: int, session_id: str) -> dict | None:
        if not self.pool:
            return None

        query = """
        SELECT user_id, session_id, search_id, query, total_count, shown_count, created_at
        FROM search_meta
        WHERE user_id = $1 AND session_id = $2
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, user_id, session_id)
        return dict(row) if row else None


    async def get_last_search_results(self, user_id: int, session_id: str) -> list[dict]:
        if not self.pool:
            return []

        query = """
        SELECT rank, document_id, source_name, source_path, score, snippet
        FROM search_results
        WHERE user_id = $1 AND session_id = $2
        ORDER BY rank ASC
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, user_id, session_id)
        return [dict(r) for r in rows]


    async def get_result_by_rank(self, user_id: int, session_id: str, rank: int) -> dict | None:
        if not self.pool:
            return None

        query = """
        SELECT rank, document_id, source_name, source_path, score, snippet
        FROM search_results
        WHERE user_id = $1 AND session_id = $2 AND rank = $3
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, user_id, session_id, rank)
        return dict(row) if row else None


    async def update_shown_count(self, user_id: int, session_id: str, shown_count: int) -> None:
        if not self.pool:
            return

        query = """
        UPDATE search_meta
        SET shown_count = $3
        WHERE user_id = $1 AND session_id = $2
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query, user_id, session_id, shown_count)


    async def reset_search_state(self, user_id: int, session_id: str) -> None:
        if not self.pool:
            return

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM search_results WHERE user_id = $1 AND session_id = $2",
                    user_id, session_id
                )
                await conn.execute(
                    "DELETE FROM search_meta WHERE user_id = $1 AND session_id = $2",
                    user_id, session_id
                )
    
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
            first_name TEXT,
            last_name TEXT,
            phone_number VARCHAR(20),
            last_seen TIMESTAMPTZ DEFAULT NOW(),
            region TEXT,
            manager_group BOOLEAN DEFAULT FALSE,
            couch_group BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query)

    async def add(
            self, 
            user_id: int, 
            username: str | None, 
            first_name: str, 
            last_name: str, 
            last_seen: datetime,
            phone_number: str | None=None):
        """добавление пользователя в таблицу"""
        logger.info(f"Added a new user: {user_id} (@{username})")
        query = """
        INSERT INTO subscribers (
            user_id, username, first_name, last_name, phone_number, 
            last_seen, region, manager_group, couch_group
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (user_id) DO UPDATE SET
            username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            phone_number = COALESCE(subscribers.phone_number, EXCLUDED.phone_number),
            last_seen = EXCLUDED.last_seen
        """
        async with self.pool.acquire() as conn:
            await conn.execute(
                query, 
                user_id, 
                username, 
                first_name, 
                last_name,
                phone_number,
                last_seen,
                None, # region
                False, # manager_group
                False # couch_group
            )

    async def get_phone(self, user_id: int) -> str | None:
        """Проверить есть ли телефон у пользователя"""
        query = "SELECT phone_number FROM subscribers WHERE user_id =$1"
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, user_id)
        return row["phone_number"] if row else None

    async def update_phone(self, user_id: int, phone_number: str):
        """Обновить номер телефона"""
        query = """
        UPDATE subscribers 
        SET phone_number = $1, last_seen = NOW()
        WHERE user_id = $2
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query, phone_number, user_id)
        logger.info(f"✓ Телефон обновлён для user_id={user_id}: {phone_number}")

    async def get_all_with_groups(self) -> list[dict]:
        """Получение всех пользователей с информацией с группами"""
        logger.info("Получаем пользователей с группами")
        query = """
        SELECT user_id, username, first_name, last_name, 
               phone_number, last_seen, created_at,
               manager_group, couch_group
        FROM subscribers
        ORDER BY last_seen DESC
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query)
        # return [r["user_id"] for r in rows]
        return [dict(r) for r in rows]
    
    async def update_user_group(self, user_id: int, group: str, value: bool):
        """Обновление принадлежности пользователя к группе"""
        if group not in ("manager_group", "couch_group"):
            raise ValueError(f"Invalid group: {group}")
        
        query = f"""
        UPDATE subscribers 
        SET {group} = $1, last_seen = NOW()
        WHERE user_id = $2
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query, value, user_id)
        
        logger.info(f"✓ Группа {group} обновлена для user_id={user_id}: {value}")


class NewsStore:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
    
    async def ensure_schema(self):
        """Создание таблицы если не существует"""
        query = """
        CREATE TABLE IF NOT EXISTS news (
            id SERIAL PRIMARY KEY,
            text TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            scheduled_at TIMESTAMPTZ,
            files JSONB,
            status VARCHAR(20) DEFAULT 'pending', -- pending | sent
            target_group VARCHAR(50) DEFAULT 'all'
        );
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query)

    async def create_news(self, text, scheduled_at=None, group="all", files=None):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO news (text, scheduled_at, target_group, files)
                VALUES ($1, $2, $3, $4)
                RETURNING id
            """, text, scheduled_at, group, json.dumps(files or []))
            return row["id"]
        
    async def get_pending_news(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM news
                WHERE status = 'pending'
            """)
            return [dict(r) for r in rows]

    async def mark_sent(self, news_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE news SET status = 'sent'
                WHERE id = $1
            """, news_id)

    async def get_all(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM news ORDER BY created_at DESC
            """)
            result = []
            try: 
                for r in rows:
                    d = dict(r)
                    files = d.get("files")
                    if files is None:
                        d["files"] = []
                    elif isinstance(files, str):
                        try:
                            d["files"] = json.loads(files)
                        except Exception:
                            d["files"] = []
                    elif isinstance(files, list):
                        d["files"] = files
                    else:
                        d["files"] = []
                    
                    if "target_group" not in d:
                        d["target_group"] = "all"
    
                    result.append(d)
            except Exception as e:
                logger.error(f"Ошибка во время получения всех новостей: {e}")

            return result

    async def get_by_id(self, news_id: int):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT * FROM news WHERE id = $1
            """, news_id)
            # return dict(row) if row else None
            if not row:
                return None
                
            news = dict(row)
            files = news.get("files")
            if files is None:
                news["files"] = []
            elif isinstance(files, str):
                try:
                    news["files"] = json.loads(files)
                except Exception:
                    news["files"] = []
            elif isinstance(files, list):
                news["files"] = files
            else:
                news["files"] = []
            
            if "target_group" not in news:
                news["target_group"] = "all"

            return news
        
    async def delete_news(self, news_id: int):
        query = "DELETE FROM news WHERE id=$1"
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(query, news_id)

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
    # Клавиатура для запроса телефона (показывается только если телефона нет)
    PHONE_KEYBOARD = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Поделиться номером телефона", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
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
    # инициализируем хранилище новостей
    news_store = NewsStore(store.pool)
    await news_store.ensure_schema()
    
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
                reply_markup=PHONE_KEYBOARD
            )

    @dp.message(F.contact)
    async def handle_contact(m: Message) -> None:
        """Обработка полученного контакта"""
        user_id = m.from_user.id
        phone = m.contact.phone_number
        
        # Сохраняем телефон в БД
        await subscriber_store.update_phone(user_id, phone)
        
        logger.info(f"✓ Получен телефон от user_id={user_id}: {phone}")
        
        # Показываем меню
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
            
            # Удаляем состояние результатов поиска
            await store.reset_search_state(user_id, session_id)
            
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
        
    DOWNLOAD_RE = re.compile(
        r'^\s*(?:скачай|пришли|отправь|документ)?\s*((?:\d+\s*[,\s]\s*)*\d+)\s*$',
        re.IGNORECASE
    )
    
    SHOW_MORE_RE = re.compile(
        r'^\s*(?:ещ[её]|покажи\s+ещ[её]|дальше|ещ[её]\s+файлы)\s*$',
        re.IGNORECASE
    )
    
    SHOW_ALL_RE = re.compile(
        r'^\s*(?:покажи\s+все|все\s+файлы|вс[её]|(дай )*все( файлы)*|полный\s+список|полный|весь|да|ага|угу|ок|окей|хорошо|хочу|конечно|да,*\s*давай|давай|покажи|показывай)\s*$',
        re.IGNORECASE
    )
    
    SHOW_ALL_RE = re.compile('|'.join(x.pattern for x in [SHOW_ALL_RE, SHOW_MORE_RE]), re.IGNORECASE)

    def parse_download_ranks(text: str) -> list[int]:
        m = DOWNLOAD_RE.match(text.strip())
        if not m:
            return []
        raw = m.group(1)
        return [int(x) for x in re.findall(r'\d+', raw)]


    def render_results(items: list[dict], total: int, offset: int = 0) -> str:
        if not items:
            return "Ничего не нашёл."

        lines = []
        for i, item in enumerate(items, start=offset + 1):
            title = html_module.escape(item["source_name"])
            snippet = (item.get("snippet") or "").strip().replace("\n", " ")
            if len(snippet) > 180:
                snippet = snippet[:177] + "..."
            snippet = html_module.escape(snippet)

            block = f"<b>{i}. {title}</b>"
            if snippet:
                block += f"\n{snippet}"
            lines.append(block)

        text = "\n\n".join(lines)

        shown = offset + len(items)
        if shown < total:
            text += f"\n\nПоказано {shown} из {total}. Напишите <b>ещё</b> или <b>покажи все</b>."
        else:
            text += "\n\nНапишите номер документа, чтобы скачать его."

        return text
    
    def extract_bot_contract(answer: str) -> dict | None:
        if not answer:
            return None

        m = re.search(
            r"<bot_contract>\s*(\{.*?\})\s*</bot_contract>",
            answer,
            flags=re.DOTALL,
        )
        if not m:
            return None

        raw_json = m.group(1).strip()

        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as e:
            logger.error(f"Не удалось распарсить bot_contract JSON: {e}")
            return None

        if not isinstance(data, dict):
            return None

        if data.get("mode") != "search_results":
            return None

        results = data.get("results")
        if not isinstance(results, list):
            return None

        return data
        
    def normalize_contract_results(contract: dict) -> list[dict]:
        results = contract.get("results", [])
        normalized = []

        for item in results:
            if not isinstance(item, dict):
                continue

            document_id = item.get("document_id")
            source_name = item.get("source_name")
            if not document_id or not source_name:
                continue

            is_relevant = bool(item.get("is_relevant"))
            new_rank = item.get("new_rank")
            old_rank = item.get("old_rank")
            snippet = (item.get("snippet") or "").strip()
            source_path = item.get("source_path")

            if is_relevant and not isinstance(new_rank, int):
                continue

            normalized.append({
                "document_id": document_id,
                "source_name": str(source_name),
                "source_path": str(source_path) if source_path else None,
                "score": None,  # при желании можно позже сохранить score отдельно
                "snippet": snippet[:500],
                "rank": new_rank if is_relevant else 10_000 + (old_rank or 0),
                "old_rank": old_rank,
                "new_rank": new_rank,
                "is_relevant": is_relevant,
            })

        # сначала релевантные по new_rank, потом нерелевантные
        normalized.sort(
            key=lambda x: (
                not x["is_relevant"],
                x["rank"] if isinstance(x["rank"], int) else 10_000,
            )
        )

        # перенумеровываем только релевантные для UI
        relevant = [x for x in normalized if x["is_relevant"]]
        for idx, item in enumerate(relevant, start=1):
            item["rank"] = idx

        return relevant

    def _extract_textish(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, list):
            parts = [_extract_textish(v) for v in value]
            return " ".join(x for x in parts if x).strip()
        if isinstance(value, dict):
            preferred_keys = [
                "text", "content", "snippet", "chunk_text", "body",
                "section", "summary", "description", "comment"
            ]
            parts = []
            for key in preferred_keys:
                if key in value:
                    txt = _extract_textish(value.get(key))
                    if txt:
                        parts.append(txt)
            if parts:
                return " ".join(parts).strip()
        return ""


    def _find_tool_payloads(events: list[dict]) -> list[dict]:
        payloads = []

        for event in events or []:
            if not isinstance(event, dict):
                continue

            content = event.get("content")
            if isinstance(content, dict):
                for part in content.get("parts", []) or []:
                    if not isinstance(part, dict):
                        continue

                    # старые варианты
                    if "tool_response" in part and isinstance(part["tool_response"], dict):
                        payloads.append(part["tool_response"])
                    if "function_response" in part and isinstance(part["function_response"], dict):
                        payloads.append(part["function_response"])

                    # новые варианты camelCase
                    if "toolResponse" in part and isinstance(part["toolResponse"], dict):
                        payloads.append(part["toolResponse"])
                    if "functionResponse" in part and isinstance(part["functionResponse"], dict):
                        payloads.append(part["functionResponse"])

            actions = event.get("actions")
            if isinstance(actions, dict):
                for key in ("function_response", "functionResponse", "tool_response", "toolResponse"):
                    value = actions.get(key)
                    if isinstance(value, dict):
                        payloads.append(value)

        return payloads

    def _collect_candidate_dicts(obj: Any, out: list[dict]) -> None:
        if isinstance(obj, dict):
            # Похоже на один candidate/result/chunk
            keys = set(obj.keys())
            if (
                "document_id" in keys
                or "source_name" in keys
                or "source_path" in keys
                or "metadata" in keys
                or "content" in keys
                or "chunk_text" in keys
            ):
                out.append(obj)

            for v in obj.values():
                _collect_candidate_dicts(v, out)

        elif isinstance(obj, list):
            for item in obj:
                _collect_candidate_dicts(item, out)


    def extract_search_results_from_events(events: list[dict]) -> list[dict]:
        """
        Извлекает document-level результаты kb_search из events.
        Приоритет:
        1. functionResponse.response.structuredContent.results
        2. functionResponse.response.structured_content.results
        3. fallback на старую эвристику
        """
        payloads = _find_tool_payloads(events)

        # 1. Нормальный путь: structuredContent
        for payload in payloads:
            if not isinstance(payload, dict):
                continue

            if payload.get("name") != "kb_search":
                continue

            response = payload.get("response")
            if not isinstance(response, dict):
                continue

            structured = response.get("structuredContent") or response.get("structured_content")
            if not isinstance(structured, dict):
                continue

            results = structured.get("results")
            if not isinstance(results, list):
                continue

            docs_by_id: dict[str, dict] = {}

            for item in results:
                if not isinstance(item, dict):
                    continue

                document_id = item.get("document_id")
                if not document_id:
                    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                    document_id = metadata.get("document_id") or metadata.get("DOCUMENT_ID")

                if not document_id:
                    continue

                source_name = (
                    item.get("source_name")
                    or item.get("source")
                    or item.get("filename")
                )

                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                if not source_name:
                    source_name = (
                        metadata.get("source_name")
                        or metadata.get("source")
                        or metadata.get("filename")
                        or metadata.get("file_name")
                        or metadata.get("name")
                    )

                source_path = (
                    item.get("relative_path")
                    or item.get("source_path")
                    or metadata.get("relative_path")
                    or metadata.get("source_path")
                )

                if not source_name:
                    if source_path:
                        source_name = str(source_path).split("/")[-1]
                    else:
                        source_name = f"{document_id}.file"

                score = item.get("score")
                numeric_score = float(score) if isinstance(score, (int, float)) else None

                snippet = (
                    _extract_textish(item.get("snippet"))
                    or _extract_textish(item.get("content"))
                    or _extract_textish(metadata.get("snippet"))
                )

                existing = docs_by_id.get(document_id)
                if not existing:
                    docs_by_id[document_id] = {
                        "document_id": document_id,
                        "source_name": str(source_name),
                        "source_path": str(source_path) if source_path else None,
                        "score": numeric_score,
                        "snippet": snippet[:500] if snippet else "",
                    }
                    continue

                existing_score = existing.get("score")
                if numeric_score is not None and (existing_score is None or numeric_score > existing_score):
                    existing["score"] = numeric_score
                    if snippet:
                        existing["snippet"] = snippet[:500]

                if not existing.get("source_path") and source_path:
                    existing["source_path"] = str(source_path)

            docs = list(docs_by_id.values())
            docs.sort(
                key=lambda x: (
                    x["score"] is not None,
                    x["score"] if x["score"] is not None else float("-inf"),
                    x["source_name"].lower(),
                ),
                reverse=True,
            )

            for idx, doc in enumerate(docs, start=1):
                doc["rank"] = idx

            return docs

        # 2. fallback: старая эвристика
        raw_candidates: list[dict] = []
        for payload in payloads:
            _collect_candidate_dicts(payload, raw_candidates)

        if not raw_candidates:
            return []

        docs_by_id: dict[str, dict] = {}

        for item in raw_candidates:
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}

            document_id = (
                item.get("document_id")
                or metadata.get("document_id")
                or metadata.get("DOCUMENT_ID")
            )
            if not document_id:
                continue

            source_name = (
                item.get("source_name")
                or metadata.get("source_name")
                or metadata.get("source")
                or metadata.get("filename")
                or metadata.get("file_name")
                or metadata.get("name")
            )
            if not source_name:
                source_path = item.get("source_path") or metadata.get("source_path") or metadata.get("relative_path")
                if source_path:
                    source_name = str(source_path).split("/")[-1]
                else:
                    source_name = f"{document_id}.file"

            source_path = (
                item.get("source_path")
                or metadata.get("source_path")
                or metadata.get("relative_path")
            )

            score = item.get("score")
            if score is None:
                score = metadata.get("score")

            snippet = (
                _extract_textish(item.get("content"))
                or _extract_textish(item.get("chunk_text"))
                or _extract_textish(item.get("text"))
                or _extract_textish(item.get("snippet"))
                or _extract_textish(metadata.get("snippet"))
            )

            existing = docs_by_id.get(document_id)
            if not existing:
                docs_by_id[document_id] = {
                    "document_id": document_id,
                    "source_name": str(source_name),
                    "source_path": str(source_path) if source_path else None,
                    "score": float(score) if isinstance(score, (int, float)) else None,
                    "snippet": snippet[:500] if snippet else "",
                }
                continue

            existing_score = existing.get("score")
            numeric_score = float(score) if isinstance(score, (int, float)) else None
            if numeric_score is not None and (existing_score is None or numeric_score > existing_score):
                existing["score"] = numeric_score
                if snippet:
                    existing["snippet"] = snippet[:500]

            if not existing.get("source_path") and source_path:
                existing["source_path"] = str(source_path)

        docs = list(docs_by_id.values())
        docs.sort(
            key=lambda x: (
                x["score"] is not None,
                x["score"] if x["score"] is not None else float("-inf"),
                x["source_name"].lower(),
            ),
            reverse=True,
        )

        for idx, doc in enumerate(docs, start=1):
            doc["rank"] = idx

        return docs

    async def handle_download_by_ranks(
        m: Message,
        store: PostgresChatStore,
        doc_handler: DocumentHandler,
        user_id: int,
        session_id: str,
        ranks: list[int],
    ) -> bool:
        if not ranks:
            return False

        sent_any = False
        for rank in ranks:
            item = await store.get_result_by_rank(user_id, session_id, rank)
            if not item:
                await m.answer(f"Не нашёл документ №{rank} в последнем списке.")
                continue

            doc_id = item.get("document_id")
            if not doc_id:
                await m.answer(f"Не удалось определить document_id для документа №{rank}.")
                continue

            file_path = None
            try:
                file_path = await doc_handler.download_document(doc_id)
                if file_path and file_path.exists():
                    await m.answer_document(
                        FSInputFile(str(file_path), filename=file_path.name)
                    )
                    sent_any = True
                else:
                    await m.answer(f"Не удалось загрузить документ №{rank}.")
            except Exception as e:
                logger.error(f"Ошибка отправки документа rank={rank}, doc_id={doc_id}: {e}", exc_info=True)
                await m.answer(f"Ошибка при загрузке документа №{rank}.")
            finally:
                try:
                    if file_path and file_path.exists():
                        file_path.unlink()
                except Exception:
                    pass

        return sent_any


    async def handle_show_all(
        m: Message,
        store: PostgresChatStore,
        user_id: int,
        session_id: str,
    ) -> bool:
        items = await store.get_last_search_results(user_id, session_id)
        if not items:
            return False

        text = render_results(items, total=len(items), offset=0)
        await store.update_shown_count(user_id, session_id, len(items))
        await m.answer(text, parse_mode="HTML")
        return True


    async def handle_show_more(
        m: Message,
        store: PostgresChatStore,
        user_id: int,
        session_id: str,
        page_size: int = SHOW_MAX,
    ) -> bool:
        meta = await store.get_last_search_meta(user_id, session_id)
        items = await store.get_last_search_results(user_id, session_id)

        if not meta or not items:
            return False

        start = meta["shown_count"]
        end = min(start + page_size, len(items))

        if start >= len(items):
            await m.answer("Это уже все найденные файлы.")
            return True

        chunk = items[start:end]
        text = render_results(chunk, total=len(items), offset=start)
        await store.update_shown_count(user_id, session_id, end)
        await m.answer(text, parse_mode="HTML")
        return True
        
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
                reply_markup=PHONE_KEYBOARD
            )
        else: 

            session_id = "default"
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
                if SHOW_MORE_RE.match(user_text) and SHOW_BY_PAGE:
                    handled = await handle_show_more(
                        m=m,
                        store=store,
                        user_id=user_id,
                        session_id=session_id,
                        page_size=SHOW_MAX,
                    )
                    if handled:
                        return

                # 2. follow-up: show all
                if SHOW_ALL_RE.match(user_text):
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
                        shown_count=min(8, len(reranked_items)),
                    )
                    logger.info(f"💾 Сохранён search-state из bot_contract: {len(reranked_items)} документов для user_id={user_id}")

                    if reranked_items:
                        top_items = reranked_items[:8]
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
                    await m.answer(html_answer, parse_mode="HTML")

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
    
    async def get_filtered_users(target_group: str = "all"):
        all_users = await subscriber_store.get_all_with_groups()

        filtered_users = []
        for user in all_users:
            user_id = user["user_id"]
            if target_group == "all":
                filtered_users.append(user_id)
            elif target_group == "manager_group" and user.get("manager_group"):
                filtered_users.append(user_id)
            elif target_group == "couch_group" and user.get("couch_group"):
                filtered_users.append(user_id)
        return filtered_users, len(all_users)

    def split_message(text: str, limit: int = 4000):
        return [text[i:i+limit] for i in range(0, len(text), limit)]

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
                        # await send_now(news['text'], file_data, target_group)
                        await send_now(safe_html, file_data, target_group)
                        await news_store.mark_sent(news["id"])

                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Error during news_scheduler like this {e}")
    
    def html_to_telegram(html: str) -> str:
        if not html:
            return ""

        # переносы строк
        html = html.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")

        # параграфы
        html = re.sub(r"<p[^>]*>", "", html)
        html = html.replace("</p>", "\n")

        # заголовки → bold
        html = re.sub(r"<h[1-6][^>]*>", "<b>", html)
        html = re.sub(r"</h[1-6]>", "</b>\n", html)

        # списки
        html = html.replace("<ul>", "").replace("</ul>", "")
        html = html.replace("<ol>", "").replace("</ol>", "")
        html = re.sub(r"<li[^>]*>", "• ", html)
        html = html.replace("</li>", "\n")

        # bold / italic
        html = html.replace("<strong>", "<b>").replace("</strong>", "</b>")
        html = html.replace("<em>", "<i>").replace("</em>", "</i>")

        # underline / strike
        html = html.replace("<u>", "<u>").replace("</u>", "</u>")
        html = html.replace("<s>", "<s>").replace("</s>", "</s>")

        # code block
        html = re.sub(r'<pre.*?>', '<pre>', html)
        html = re.sub(r'</pre>', '</pre>\n', html)

        # удалить ВСЕ лишние теги (очень важно)
        allowed_tags = ["b", "i", "u", "s", "a", "code", "pre"]

        def clean_tags(match):
            tag = match.group(1)
            if tag.split()[0].lower() in allowed_tags:
                return match.group(0)
            return ""

        html = re.sub(r"</?([^>\s]+)[^>]*>", clean_tags, html)

        # HTML entities
        html = html.replace("&nbsp;", " ")
        html = html.replace("&amp;", "&")

        # лишние переносы
        html = re.sub(r"\n{3,}", "\n\n", html)

        return html.strip()

    @broadcast_app.post("/broadcast")
    async def broadcast(
        # text: str = Form(...),
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
                    file_path = os.path.join(UPLOAD_NEWS, f"{f.filename}")
                    
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
                # news_id = await news_store.create_news(text, schedule_dt, files=file_paths, group=target_group)
                # news_id = await news_store.create_news(safe_html, schedule_dt, files=file_paths, group=target_group)
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
            
            if group not in ("manager_group", "couch_group"):
                raise HTTPException(400, "Invalid group")
            
            await subscriber_store.update_user_group(user_id, group, value)
            
            return {"status": "ok", "user_id": user_id, "group": group, "value": value}
        except Exception as e:
            raise HTTPException(400, str(e))

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
        asyncio.create_task(news_scheduler())
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