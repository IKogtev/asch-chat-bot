import os
from datetime import datetime, timezone
import threading
import asyncpg
import json
from utils import setup_logger
logger = setup_logger('event_logger', 'event_logger.log')
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://aszh-bot:aszh-bot@postgres:5432/aszh-bot")

class EventLogger:
    """Обработчик событий"""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        """Singleton чтобы не плодить подключения"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance.pool = None
        return cls._instance

    async def init(self):
        """Инициализация пула соединений"""
        if self.pool is not None:
            return

        self.pool = await asyncpg.create_pool(
            dsn=DATABASE_URL,
            min_size=2,
            max_size=10
        )

        await self._ensure_table()

    # =========================
    # 🔧 INIT DB
    # =========================
    async def _ensure_table(self):
        """Создаёт таблицу если её нет"""
        logger.info("Инициализация таблицы для логирования событий...")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE EXTENSION IF NOT EXISTS "pgcrypto";
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

                    user_id TEXT,
                    user_name TEXT,
                    session_id TEXT,

                    event_type TEXT NOT NULL,
                    channel TEXT,

                    payload JSONB,

                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
                );
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_user_id ON events(user_id);
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type);
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);
            """)

    # =========================
    # 📝 LOG EVENT
    # =========================
    async def log_event(
        self,
        event_type: str,
        user_id: str = None,
        user_name: str = None,
        session_id: str = None,
        channel: str = None,
        payload: dict = None,
    ):
        if self.pool is None:
            return  # защита от вызова до init
        logger.info(f"Логирование события: {event_type}")
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO events (
                        user_id,
                        user_name,
                        session_id,
                        event_type,
                        channel,
                        payload,
                        created_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    user_id,
                    user_name,
                    session_id,
                    event_type,
                    channel,
                    json.dumps(payload or {}),
                    datetime.now(timezone.utc),
                )

        except Exception as e:
            logger.error(f"[EVENT_LOGGER_ERROR] {e}")

    # =========================
    # 📊 GET EVENTS (для UI)
    # =========================
    async def get_events(self, limit: int = 100):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT user_id, user_name, event_type, channel, payload, created_at
                FROM events
                ORDER BY created_at DESC
                LIMIT $1
            """, limit)

            return [
                {
                    "user_id": r["user_id"],
                    "user_name": r["user_name"],
                    "event_type": r["event_type"],
                    "channel": r["channel"],
                    "payload": r["payload"],
                    "created_at": r["created_at"].isoformat(),
                }
                for r in rows
            ]