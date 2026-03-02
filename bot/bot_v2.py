import asyncio
import os
from typing import Any, Dict, List, Literal, Optional, Tuple

import aiohttp
import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv

Role = Literal["user", "model"]


class PostgresChatStore:
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

    async def append(self, user_id: int, role: Role, text: str) -> None:
        assert self.pool is not None
        sql = "INSERT INTO chat_messages (user_id, role, text) VALUES ($1, $2, $3)"
        async with self.pool.acquire() as conn:
            await conn.execute(sql, user_id, role, text)

    async def reset(self, user_id: int) -> None:
        assert self.pool is not None
        sql = "DELETE FROM chat_messages WHERE user_id = $1"
        async with self.pool.acquire() as conn:
            await conn.execute(sql, user_id)


class AdkApiClient:
    """
    Под твою OpenAPI:
      - GET  /list-apps
      - POST /apps/{app_name}/users/{user_id}/sessions/{session_id}
      - POST /run

    /run может ожидать snake_case или camelCase — сделаем fallback.
    """

    def __init__(self, base_url: str, app_name: str, timeout_s: int = 120):
        self.base_url = base_url.rstrip("/")
        self.app_name = app_name
        self.timeout = aiohttp.ClientTimeout(total=timeout_s)
        self.http: Optional[aiohttp.ClientSession] = None

    async def open(self) -> None:
        if self.http is None:
            self.http = aiohttp.ClientSession(timeout=self.timeout)

    async def close(self) -> None:
        if self.http:
            await self.http.close()
            self.http = None

    async def ensure_session(self, user_id: str, session_id: str) -> None:
        assert self.http is not None
        url = f"{self.base_url}/apps/{self.app_name}/users/{user_id}/sessions/{session_id}"
        async with self.http.post(url, json={}) as resp:
            if resp.status in (200, 201):
                return
            # если уже существует — некоторые реализации возвращают 409/400
            if resp.status in (400, 409):
                try:
                    data = await resp.json()
                    detail = (data.get("detail") or "").lower()
                    if "exists" in detail or "already" in detail:
                        return
                except Exception:
                    pass
            raise RuntimeError(f"ADK ensure_session failed: {resp.status} {await resp.text()}")

    async def run(self, user_id: str, session_id: str, text: str) -> str:
        assert self.http is not None
        url = f"{self.base_url}/run"

        # пробуем оба формата payload: snake_case и camelCase
        payloads: List[Dict[str, Any]] = [
            {
                "app_name": self.app_name,
                "user_id": user_id,
                "session_id": session_id,
                "new_message": {"role": "user", "parts": [{"text": text}]},
            },
            {
                "appName": self.app_name,
                "userId": user_id,
                "sessionId": session_id,
                "newMessage": {"role": "user", "parts": [{"text": text}]},
            },
        ]

        last_err: Optional[Tuple[int, str]] = None
        for payload in payloads:
            async with self.http.post(url, json=payload) as resp:
                if resp.status == 200:
                    events = await resp.json()
                    return self._extract_model_text(events) or "Пустой ответ от агента."
                last_err = (resp.status, await resp.text())

        raise RuntimeError(f"ADK /run failed: {last_err[0]} {last_err[1]}")  # type: ignore[index]

    @staticmethod
    def _extract_model_text(events: Any) -> str:
        if not isinstance(events, list):
            return ""
        for ev in reversed(events):
            content = (ev or {}).get("content") or {}
            if content.get("role") != "model":
                continue
            parts = content.get("parts") or []
            texts = []
            for p in parts:
                t = p.get("text")
                if isinstance(t, str) and t.strip():
                    texts.append(t)
            if texts:
                return "".join(texts).strip()
        return ""


async def main() -> None:
    load_dotenv()

    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not tg_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in .env")

    dsn = (os.getenv("POSTGRES_DSN") or os.getenv("DATABASE_URL") or "").strip()
    if not dsn:
        raise RuntimeError("POSTGRES_DSN or DATABASE_URL is missing in .env")

    adk_base = os.getenv("ADK_API_BASE", "http://127.0.0.1:8000").strip()
    # По твоему /list-apps app называется "agent"
    adk_app = os.getenv("ADK_APP_NAME", "agent").strip()

    bot = Bot(token=tg_token)
    dp = Dispatcher()

    store = PostgresChatStore(dsn=dsn, max_turns=30)
    await store.connect()
    await store.ensure_schema()

    adk = AdkApiClient(base_url=adk_base, app_name=adk_app)
    await adk.open()

    @dp.message(Command("start"))
    async def start(m: Message) -> None:
        await m.answer(
            f"Привет! Я бот через Google ADK.\n"
            f"Использую app: {adk_app}\n"
            f"Команды: /reset — сбросить диалог."
        )

    @dp.message(Command("reset"))
    async def reset(m: Message) -> None:
        # сбрасываем только лог в БД; контекст в ADK можно “сбросить” сменой session_id
        await store.reset(m.from_user.id)
        await m.answer("Ок, локальный лог сброшен 🙂\n(Если нужно сбросить контекст агента — скажи, сделаю reset через новую session_id.)")

    @dp.message(F.text)
    async def on_text(m: Message) -> None:
        user_id = str(m.from_user.id)
        session_id = "default"  # можно сделать str(m.chat.id) для групп
        user_text = (m.text or "").strip()
        if not user_text:
            return

        await adk.ensure_session(user_id=user_id, session_id=session_id)
        answer = await adk.run(user_id=user_id, session_id=session_id, text=user_text)

        await store.append(m.from_user.id, "user", user_text)
        await store.append(m.from_user.id, "model", answer)

        await m.answer(answer)

    try:
        await dp.start_polling(bot)
    finally:
        await adk.close()
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())