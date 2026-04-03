import asyncpg
import aiohttp
import json
from typing import Optional
from uuid import uuid4
# Импортируем логгер из
from utils import setup_logger
from bot.services.config import Settings
from datetime import datetime

# Настройка логгера
logger = setup_logger('database', 'db.log')
##############################################
# Работа с Базами данных через классы-обертки
##############################################
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
        """Schema is owned by Alembic; run ``alembic upgrade head`` before the app.

        Revision ``a1b2c3d4e5f6`` (``alembic/versions/a1b2c3d4e5f6_initial_schema.py``) replaces
        the former inline DDL below (kept only as reference).
        """
        if not self.pool:
            logger.error("Pool не инициализирован")
            raise RuntimeError("Pool not initialized")
        # query = """
        # CREATE TABLE IF NOT EXISTS chat_history (
        #     id SERIAL PRIMARY KEY,
        #     user_id BIGINT NOT NULL,
        #     role TEXT NOT NULL,
        #     content TEXT NOT NULL,
        #     created_at TIMESTAMP DEFAULT NOW()
        # );
        # CREATE INDEX IF NOT EXISTS idx_user_id ON chat_history(user_id);
        # CREATE INDEX IF NOT EXISTS idx_created_at ON chat_history(created_at);
        #
        # CREATE TABLE IF NOT EXISTS search_meta (
        #     user_id BIGINT NOT NULL,
        #     session_id TEXT NOT NULL,
        #     search_id TEXT NOT NULL,
        #     query TEXT NOT NULL,
        #     total_count INT NOT NULL,
        #     shown_count INT NOT NULL DEFAULT 0,
        #     created_at TIMESTAMP DEFAULT NOW(),
        #     PRIMARY KEY (user_id, session_id)
        # );
        #
        # CREATE TABLE IF NOT EXISTS search_results (
        #     id SERIAL PRIMARY KEY,
        #     user_id BIGINT NOT NULL,
        #     session_id TEXT NOT NULL,
        #     search_id TEXT NOT NULL,
        #     rank INT NOT NULL,
        #     document_id TEXT NOT NULL,
        #     source_name TEXT NOT NULL,
        #     source_path TEXT,
        #     score DOUBLE PRECISION,
        #     snippet TEXT,
        #     created_at TIMESTAMP DEFAULT NOW()
        # );
        #
        # CREATE INDEX IF NOT EXISTS idx_search_results_user_session
        # ON search_results(user_id, session_id, rank);
        #
        # CREATE INDEX IF NOT EXISTS idx_search_results_search_id
        # ON search_results(search_id);
        # """
        # try:
        #     async with self.pool.acquire() as conn:
        #         await conn.execute(query)
        #     logger.info("Схема БД проверена/создана")
        # except Exception as e:
        #     logger.error(f"Ошибка создания схемы: {e}", exc_info=True)
        #     raise
        pass

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
        shown_count: int = Settings.SHOW_MAX,
    ) -> str:
        """Сохранение результатов поиска"""
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
        """Получаем последнюю метаинформацию"""
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
        """Получаем последние результаты поиска"""
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
        """Получаем результаты поиска по рангу"""
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
        """Обновляем количество показанных"""
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
        """Сбрасываем состояние поиска"""
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
        """Schema is owned by Alembic; see revision ``a1b2c3d4e5f6``."""
        # query = """
        # CREATE TABLE IF NOT EXISTS subscribers (
        #     user_id BIGINT PRIMARY KEY,
        #     username TEXT,
        #     first_name TEXT,
        #     last_name TEXT,
        #     phone_number VARCHAR(20),
        #     last_seen TIMESTAMPTZ DEFAULT NOW(),
        #     region TEXT,
        #     manager_group BOOLEAN DEFAULT FALSE,
        #     coach_group BOOLEAN DEFAULT FALSE,
        #     created_at TIMESTAMPTZ DEFAULT NOW()
        # );
        # """
        # async with self.pool.acquire() as conn:
        #     await conn.execute(query)
        pass

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
            last_seen, region, manager_group, coach_group
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
                False # coach_group
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
               manager_group, coach_group
        FROM subscribers
        ORDER BY last_seen DESC
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query)
        return [dict(r) for r in rows]

    async def get_user_data(self, user_id: int) -> dict | None:
        """Получение полных данных пользователя для передачи в ADK"""
        query = """
        SELECT user_id, username, first_name, last_name, phone_number,
               region, manager_group, coach_group
        FROM subscribers
        WHERE user_id = $1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, user_id)

        if not row:
            return None

        return {
            "user_id": row["user_id"],
            "username": row["username"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "phone_number": row["phone_number"],
            "region": row["region"],
            "manager_group": row["manager_group"],
            "coach_group": row["coach_group"]
        }

    async def update_user_group(self, user_id: int, group: str, value: bool):
        """Обновление принадлежности пользователя к группе"""
        if group not in ("manager_group", "coach_group"):
            raise ValueError(f"Invalid group: {group}")

        query = f"""
        UPDATE subscribers
        SET {group} = $1, last_seen = NOW()
        WHERE user_id = $2
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query, value, user_id)

        logger.info(f"✓ Группа {group} обновлена для user_id={user_id}: {value}")
# Хранилище новостей и рассылок
class NewsStore:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def ensure_schema(self):
        """Schema is owned by Alembic; see revision ``a1b2c3d4e5f6``."""
        # query = """
        # CREATE TABLE IF NOT EXISTS news (
        #     id SERIAL PRIMARY KEY,
        #     text TEXT NOT NULL,
        #     created_at TIMESTAMPTZ DEFAULT NOW(),
        #     scheduled_at TIMESTAMPTZ,
        #     files JSONB,
        #     status VARCHAR(20) DEFAULT 'pending', -- pending | sent
        #     target_group VARCHAR(50) DEFAULT 'all'
        # );
        # """
        # async with self.pool.acquire() as conn:
        #     await conn.execute(query)
        pass

    async def create_news(self, text, scheduled_at=None, group="all", files=None):
        """Создание новостей для рассылки"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO news (text, scheduled_at, target_group, files)
                VALUES ($1, $2, $3, $4)
                RETURNING id
            """, text, scheduled_at, group, json.dumps(files or []))
            return row["id"]

    async def get_pending_news(self):
        """Получение новостей в ожидании к отправке"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM news
                WHERE status = 'pending'
            """)
            return [dict(r) for r in rows]

    async def mark_sent(self, news_id: int):
        """Отметить новость как отправленную"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE news SET status = 'sent'
                WHERE id = $1
            """, news_id)

    def _parse_news_row(self, row: asyncpg.Record) -> dict:
        """Приватный метод для нормализации данных новости из БД"""
        d = dict(row)
        files = d.get("files")

        if isinstance(files, str):
            try:
                d["files"] = json.loads(files)
            except json.JSONDecodeError:
                logger.error(f"Failed to parse files JSON for news_id={d.get('id')}")
                d["files"] = []
        elif isinstance(files, list):
            d["files"] = files
        else:
            d["files"] = []

        d.setdefault("target_group", "all")
        return d

    async def get_all(self):
        """Получение всех новостей"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM news ORDER BY created_at DESC
            """)
            result = []
            for r in rows:
                try:
                    result.append(self._parse_news_row(r))
                except Exception as e:
                    logger.error(f"Ошибка во время получения всех новостей: {e}")

            return result

    async def get_by_id(self, news_id: int):
        """Получение новости по ID"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT * FROM news WHERE id = $1
            """, news_id)
            if not row:
                return None
            return self._parse_news_row(row)

    async def delete_news(self, news_id: int):
        """Удаление новости по ID"""
        query = "DELETE FROM news WHERE id=$1"
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(query, news_id)
# Хранилище для взаимодействия с ADK API
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

    async def set_user_state(self, user_id: str, session_id: str, user_data: dict) -> None:
        """Установка данных пользователя через system message"""
        if not self.http:
            raise RuntimeError("HTTP session not initialized")

        first_name = (user_data.get("first_name") or "").strip()
        last_name = (user_data.get("last_name") or "").strip()
        username = (user_data.get("username") or "").strip()
        phone = user_data.get("phone_number") or "не указан"
        region = user_data.get("region") or "не указан"
        is_manager = "да" if user_data.get("manager_group") else "нет"
        is_coach = "да" if user_data.get("coach_group") else "нет"

        display_name = first_name or username or "пользователь"

        # Формируем структурированное сообщение с данными
        user_context = (
            f"Контекст пользователя:\n"
            f"Имя: {display_name}\n"
            f"Полное имя: {first_name} {last_name}\n"
            f"Username: @{username if username else 'не указан'}\n"
            f"Телефон: {phone}\n"
            f"Регион: {region}\n"
            f"Роль менеджера: {is_manager}\n"
            f"Роль коуча: {is_coach}\n\n"
            f"Обращайся к пользователю по имени '{display_name}' в дальнейшем диалоге."
        )

        url = f"{self.base_url}/run"

        payload = {
            "app_name": self.app_name,
            "user_id": user_id,
            "session_id": session_id,
            "new_message": {
                "role": "system",
                "parts": [{"text": user_context}]
            }
        }

        try:
            async with self.http.post(url, json=payload) as resp:
                if resp.status == 200:
                    logger.info(f"✅ Контекст пользователя установлен для user={user_id} ({display_name})")
                    logger.debug(f"Отправлен контекст: {user_context}")
                else:
                    text = await resp.text()
                    logger.warning(f"⚠️ Не удалось установить контекст: {resp.status} - {text}")
        except aiohttp.ClientError as e:
            logger.error(f"Ошибка при установке контекста: {e}", exc_info=True)
