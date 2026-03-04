import asyncio
import json
import os
from typing import Optional
from pathlib import Path

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.types import FSInputFile
from dotenv import load_dotenv
import aiohttp

# Загружаем переменные окружения ДО импорта setup_logger
load_dotenv(override=True)

from utils import setup_logger

# Настройка логгера
logger = setup_logger('bot', 'bot.log')

# Пытаемся подключить контракт (pydantic)
try:
    from contracts.agent_response_v1 import AgentResponse
except Exception:
    AgentResponse = None  # fallback без pydantic

KB_ROOT = Path("/app/kb_storage").resolve()

def safe_resolve_kb_path(rel_path: str) -> Path:
    rel_path = (rel_path or "").strip().lstrip("/\\")
    p = (KB_ROOT / rel_path).resolve()

    # защита от ../ и выхода за корень
    if KB_ROOT != p and KB_ROOT not in p.parents:
        raise ValueError(f"Path traversal blocked: {rel_path}")

    return p

def coerce_to_contract(answer_text: str) -> dict:
    """
    Гарантирует возврат валидного payload контракта.
    Никогда не выбрасывает исключение наружу.
    """
    try:
        data = json.loads(answer_text)
        if not isinstance(data, dict):
            raise ValueError("Not object")

        for k in ("contract_version", "answer", "attachments"):
            if k not in data:
                raise ValueError(f"Missing {k}")

        if not isinstance(data["attachments"], list):
            raise ValueError("attachments must be list")

        return data
    except Exception:
        # fallback — оборачиваем в контракт
        return {
            "contract_version": "1.0",
            "answer": (answer_text or "").strip() or "Готово.",
            "attachments": [],
        }

async def send_attachments(m: Message, attachments: list[dict]) -> None:
    for a in attachments:
        rel = (a.get("path") or "").strip()
        title = (a.get("title") or Path(rel).name).strip() or "Документ"

        if not rel:
            continue

        try:
            file_path = safe_resolve_kb_path(rel)
        except Exception:
            await m.answer(f"⚠️ Небезопасный путь: {rel}")
            continue

        if not file_path.exists() or not file_path.is_file():
            await m.answer(f"⚠️ Файл не найден: {title}")
            continue

        await m.answer_document(
            document=FSInputFile(file_path),
            caption=title[:1024],
        )

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
    
    async def run(self, user_id: str, session_id: str, text: str) -> str:
        """Отправка сообщения агенту и получение ответа"""
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
            logger.debug(f"Отправка в ADK: POST {url}")
            logger.debug(f"Payload: {payload}")
            
            async with self.http.post(url, json=payload) as resp:
                text_resp = await resp.text()
                
                if resp.status != 200:
                    logger.error(f"ADK run failed: {resp.status} {text_resp}")
                    raise RuntimeError(f"ADK run failed: {resp.status}")
                
                try:
                    events = await resp.json()
                    # Добавь это логирование
                    logger.debug(f"Структура ответа ADK: {events}")
                except Exception as e:
                    logger.warning(f"Ответ не в формате JSON: {text_resp[:200]}")
                    return text_resp
                
                answer = self._extract_model_text(events)
                
                if not answer:
                    logger.warning("Пустой ответ от агента")
                    # Добавь вывод структуры для анализа
                    logger.error(f"Не удалось извлечь текст из: {events}")
                    return "Агент не вернул ответ"
                
                logger.debug(f"Получен ответ от ADK: {answer[:100]}...")
                return answer
                
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
    
    logger.info(f"Конфигурация:")
    logger.info(f"  ADK Base: {adk_base}")
    logger.info(f"  ADK App: {adk_app}")
    logger.info(f"  Database: {dsn.split('@')[1] if '@' in dsn else 'configured'}")
    
    # Инициализация компонентов
    bot = Bot(token=tg_token)
    dp = Dispatcher()
    
    store = PostgresChatStore(dsn=dsn, max_turns=30)
    await store.connect()
    await store.ensure_schema()
    
    adk = AdkApiClient(base_url=adk_base, app_name=adk_app)
    await adk.open()
    
    logger.info("Все компоненты инициализированы")
    
    # Обработчики команд
    @dp.message(Command("start"))
    async def start(m: Message) -> None:
        user_id = m.from_user.id
        username = m.from_user.username or "unknown"
        logger.info(f"Команда /start от user_id={user_id} (@{username})")
        
        await m.answer(
            f"👋 Привет! Я бот базы знаний через Google ADK.\n\n"
            f"🤖 Использую агент: {adk_app}\n\n"
            f"📋 Команды:\n"
            f"/reset — сбросить историю диалога\n"
            f"/help — показать помощь"
        )
    
    @dp.message(Command("reset"))
    async def reset(m: Message) -> None:
        user_id = m.from_user.id
        username = m.from_user.username or "unknown"
        logger.info(f"Команда /reset от user_id={user_id} (@{username})")
        
        try:
            await store.reset(user_id)
            await m.answer("✅ История диалога сброшена")
            logger.info(f"История сброшена для user_id={user_id}")
        except Exception as e:
            logger.error(f"Ошибка при сбросе истории: {e}", exc_info=True)
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
    
    @dp.message(F.text)
    async def on_text(m: Message) -> None:
        user_id = str(m.from_user.id)
        username = m.from_user.username or "unknown"
        session_id = "default"
        user_text = (m.text or "").strip()
        
        if not user_text:
            return
        
        logger.info(f"📨 Сообщение от user_id={user_id} (@{username}): {user_text[:100]}")
        
        try:
            # Создание/проверка сессии
            await adk.ensure_session(user_id=user_id, session_id=session_id)
            
            # Отправка в агент
            answer_raw = await adk.run(user_id=user_id, session_id=session_id, text=user_text)

            payload = coerce_to_contract(answer_raw)

            text_answer = (payload.get("answer") or "").strip()
            attachments = payload.get("attachments") or []

            # Сохраняем в БД уже “нормализованный контракт”
            await store.append(int(user_id), "user", user_text)
            await store.append(int(user_id), "model", json.dumps(payload, ensure_ascii=False))

            if text_answer:
                await m.answer(text_answer)

            if attachments:
                await send_attachments(m, attachments)

        except Exception as e:
            logger.error(f"❌ Ошибка обработки сообщения от user_id={user_id}: {e}", exc_info=True)
            await m.answer(
                "😔 Произошла ошибка при обработке запроса.\n"
                "Попробуйте позже или используйте /reset для сброса диалога."
            )
    
    # Запуск бота
    try:
        logger.info("🚀 Бот запущен и готов к работе")
        await dp.start_polling(bot)
    finally:
        logger.info("Остановка бота...")
        await adk.close()
        await store.close()
        await bot.session.close()
        logger.info("Бот остановлен")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки (Ctrl+C)")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        raise