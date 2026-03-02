import asyncio
import os
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv

from google import genai
from google.genai import types


Role = Literal["user", "model"]


# -----------------------------
# Storage (PostgreSQL via asyncpg)
# -----------------------------
class PostgresChatStore:
    """
    Таблица chat_messages:
      - user_id: telegram user id
      - role: 'user' | 'model'
      - text: message content
      - created_at: timestamp
    Контекст: берём последние (max_turns*2) сообщений по user_id.
    """

    def __init__(self, dsn: str, max_turns: int = 30):
        self.dsn = dsn
        self.max_turns = max_turns
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(dsn=self.dsn, min_size=1, max_size=10)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def ensure_schema(self) -> None:
        assert self.pool is not None
        sql = """
        CREATE TABLE IF NOT EXISTS chat_messages (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'model')),
            text TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_chat_messages_user_id_created_at
            ON chat_messages (user_id, created_at DESC);
        """
        async with self.pool.acquire() as conn:
            await conn.execute(sql)

    async def get(self, user_id: int) -> List[Dict[str, str]]:
        """
        Возвращает историю в хронологическом порядке (от старых к новым),
        чтобы корректно скармливать модели.
        """
        assert self.pool is not None
        limit = self.max_turns * 2
        sql = """
        SELECT role, text
        FROM chat_messages
        WHERE user_id = $1
        ORDER BY created_at DESC
        LIMIT $2
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, user_id, limit)

        # rows пришли от новых к старым — переворачиваем
        history = [{"role": r["role"], "text": r["text"]} for r in reversed(rows)]
        return history

    async def append(self, user_id: int, role: Role, text: str) -> None:
        assert self.pool is not None
        sql = """
        INSERT INTO chat_messages (user_id, role, text)
        VALUES ($1, $2, $3)
        """
        async with self.pool.acquire() as conn:
            await conn.execute(sql, user_id, role, text)

        # (опционально) чистим хвост, чтобы база не росла бесконечно
        await self._trim(user_id)

    async def reset(self, user_id: int) -> None:
        assert self.pool is not None
        sql = "DELETE FROM chat_messages WHERE user_id = $1"
        async with self.pool.acquire() as conn:
            await conn.execute(sql, user_id)

    async def _trim(self, user_id: int) -> None:
        """
        Оставляем только последние (max_turns*2) сообщений на пользователя.
        """
        assert self.pool is not None
        keep = self.max_turns * 2
        sql = """
        DELETE FROM chat_messages
        WHERE id IN (
            SELECT id
            FROM chat_messages
            WHERE user_id = $1
            ORDER BY created_at DESC
            OFFSET $2
        )
        """
        async with self.pool.acquire() as conn:
            await conn.execute(sql, user_id, keep)


# -----------------------------
# LLM Agent (google-genai)
# -----------------------------
@dataclass
class LlmConfig:
    api_key: str
    api_url: Optional[str]
    model: str


class GenAiAgent:
    def __init__(self, cfg: LlmConfig):
        if cfg.api_url and cfg.api_url.strip():
            self.client = genai.Client(
                vertexai=True,
                http_options={
                    "base_url": cfg.api_url.strip(),
                    "headers": {
                        "Authorization": f"Bearer {cfg.api_key}",
                        "x-goog-api-key": cfg.api_key,
                    },
                },
            )
        else:
            self.client = genai.Client(api_key=cfg.api_key)

        self.model = cfg.model
        self.gen_config = types.GenerateContentConfig(
            system_instruction=(
                "Ты полезный ассистент в Telegram. Отвечай по делу, "
                "поддерживай контекст диалога, не придумывай факты."
            )
        )

    @staticmethod
    def _history_to_contents(history: List[Dict[str, str]]) -> List[types.Content]:
        contents: List[types.Content] = []
        for msg in history:
            role = msg.get("role")
            text = msg.get("text", "")
            if role not in ("user", "model") or not text:
                continue
            contents.append(
                types.Content(role=role, parts=[types.Part.from_text(text=text)])
            )
        return contents

    async def reply(self, history: List[Dict[str, str]], user_text: str) -> str:
        contents = self._history_to_contents(history)
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_text)]))

        resp = await self.client.aio.models.generate_content(
            model=self.model,
            contents=contents,
            config=self.gen_config,
        )
        text = (resp.text or "").strip()
        return text or "Не получил ответ от модели. Попробуй переформулировать."


# -----------------------------
# Telegram bot (aiogram)
# -----------------------------
async def main() -> None:
    load_dotenv()

    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not tg_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in .env")

    llm_key = os.getenv("LLM_API_KEY", "").strip()
    llm_url = os.getenv("LLM_API_URL", "").strip() or None
    llm_model = os.getenv("LLM_API_MODEL", "").strip()
    if not llm_key or not llm_model:
        raise RuntimeError("LLM_API_KEY / LLM_API_MODEL is missing in .env")

    dsn = (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_DSN") or "").strip()
    if not dsn:
        raise RuntimeError("DATABASE_URL (or POSTGRES_DSN) is missing in .env")

    bot = Bot(token=tg_token)
    dp = Dispatcher()

    store = PostgresChatStore(dsn=dsn, max_turns=30)
    await store.connect()
    await store.ensure_schema()

    agent = GenAiAgent(LlmConfig(api_key=llm_key, api_url=llm_url, model=llm_model))

    @dp.message(Command("start"))
    async def start(m: Message) -> None:
        await m.answer(
            "Привет! Я бот с LLM.\n"
            "Пиши сообщение — отвечу с учётом контекста.\n"
            "Команды: /reset — сбросить диалог."
        )

    @dp.message(Command("reset"))
    async def reset(m: Message) -> None:
        await store.reset(m.from_user.id)
        await m.answer("Ок, контекст сброшен 🙂")

    @dp.message(F.text)
    async def on_text(m: Message) -> None:
        user_id = m.from_user.id
        user_text = m.text.strip()

        history = await store.get(user_id)
        answer = await agent.reply(history=history, user_text=user_text)

        await store.append(user_id, "user", user_text)
        await store.append(user_id, "model", answer)

        await m.answer(answer)

    try:
        await dp.start_polling(bot)
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())