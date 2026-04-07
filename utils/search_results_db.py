"""
Сохранение результатов doc_search в PostgreSQL (общая логика для бота и DocSearchOrchestrator).
Таблицы: search_meta, search_results (схема создаётся в PostgresChatStore.ensure_schema).
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import asyncpg

from utils.logger import setup_logger

logger = setup_logger("search_results_db", "agent.log")

_pool_lock = asyncio.Lock()
_shared_pool: Optional[asyncpg.Pool] = None


def _dsn() -> str:
    return (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_DSN") or "").strip()


async def get_shared_pool() -> Optional[asyncpg.Pool]:
    """Пул для процесса adk-agent (lazy). Если DSN не задан — None."""
    dsn = _dsn()
    if not dsn:
        return None
    global _shared_pool
    async with _pool_lock:
        if _shared_pool is None:
            try:
                _shared_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
                logger.info("search_results_db: пул PostgreSQL создан")
            except Exception as e:
                logger.error("search_results_db: не удалось подключиться к БД: %s", e, exc_info=True)
                return None
    return _shared_pool


async def save_doc_search_results(
    pool: asyncpg.Pool,
    user_id: int,
    session_id: str,
    query: str,
    items: list[dict[str, Any]],
    shown_count: int,
) -> str:
    """
    Полная замена search_meta/search_results для пары (user_id, session_id).
    items: document_id, source_name, source_path?, snippet?, rank (1..n).
    """
    search_id = str(uuid4())
    n = len(items)
    shown = min(int(shown_count), n) if n else 0

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM search_results WHERE user_id = $1 AND session_id = $2",
                user_id,
                session_id,
            )
            await conn.execute(
                "DELETE FROM search_meta WHERE user_id = $1 AND session_id = $2",
                user_id,
                session_id,
            )
            await conn.execute(
                """
                INSERT INTO search_meta (user_id, session_id, search_id, query, total_count, shown_count)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                user_id,
                session_id,
                search_id,
                query,
                n,
                shown,
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
                    int(item["rank"]),
                    str(item["document_id"]),
                    str(item["source_name"]),
                    item.get("source_path"),
                    item.get("score"),
                    (item.get("snippet") or "")[:2000],
                )
    return search_id


async def update_doc_search_shown_count(
    pool: asyncpg.Pool,
    user_id: int,
    session_id: str,
    shown_count: int,
) -> None:
    q = """
    UPDATE search_meta
    SET shown_count = $3
    WHERE user_id = $1 AND session_id = $2
    """
    async with pool.acquire() as conn:
        await conn.execute(q, user_id, session_id, shown_count)


async def load_doc_search_list_from_db(
    pool: asyncpg.Pool,
    user_id: int,
    session_id: str,
) -> Optional[Tuple[List[Dict[str, Any]], int]]:
    """
    Восстанавливает полный список документов и shown_count из search_meta/search_results
    (тот же контракт, что пишет save_doc_search_results).
    """
    async with pool.acquire() as conn:
        meta = await conn.fetchrow(
            """
            SELECT shown_count
            FROM search_meta
            WHERE user_id = $1 AND session_id = $2
            """,
            user_id,
            session_id,
        )
        if not meta:
            return None
        shown_count = int(meta["shown_count"])
        rows = await conn.fetch(
            """
            SELECT rank, document_id, source_name, source_path, score, snippet
            FROM search_results
            WHERE user_id = $1 AND session_id = $2
            ORDER BY rank ASC
            """,
            user_id,
            session_id,
        )
    if not rows:
        return None
    normalized: List[Dict[str, Any]] = []
    for r in rows:
        normalized.append(
            {
                "document_id": str(r["document_id"]),
                "source_name": str(r["source_name"]),
                "source_path": r.get("source_path"),
                "snippet": (r.get("snippet") or "")[:2000],
                "rank": int(r["rank"]),
                "score": r.get("score"),
            }
        )
    return normalized, shown_count
