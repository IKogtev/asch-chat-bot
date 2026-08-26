from typing import Any, Optional

import asyncpg

_USER_SELECT = """
        SELECT
            u.id,
            u.phone_number,
            u.is_blocked,
            ua.first_name,
            ua.last_name,
            ua.username
        FROM users u
        LEFT JOIN user_accounts ua ON ua.user_id = u.id
"""


def _user_from_row(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "phone_number": row["phone_number"],
        "is_blocked": bool(row["is_blocked"]),
        "first_name": row["first_name"] or "",
        "last_name": row["last_name"] or "",
        "username": row["username"] or "",
    }


async def get_user_by_id(pool: asyncpg.Pool, user_id: str) -> Optional[dict[str, Any]]:
    query = _USER_SELECT + """
        WHERE u.id = $1
        ORDER BY ua.last_seen DESC NULLS LAST
        LIMIT 1
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, user_id)
    if row is None:
        return None
    return _user_from_row(row)


async def get_user_by_phone(pool: asyncpg.Pool, phone: str) -> Optional[dict[str, Any]]:
    query = _USER_SELECT + """
        WHERE u.phone_number = $1
        ORDER BY ua.last_seen DESC NULLS LAST
        LIMIT 1
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, phone)
    if row is None:
        return None
    return _user_from_row(row)


def profile_for_adk(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "first_name": user.get("first_name") or "",
        "last_name": user.get("last_name") or "",
        "username": user.get("username") or "",
        "region": "",
        "manager_group": False,
        "coach_group": False,
    }
