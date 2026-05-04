import asyncpg
from typing import Optional
from uuid import UUID
from bot.services.utils import normalize_phone

# обработчик пользователя

class UserResolver:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def resolve_user(
        self,
        platform: str,
        platform_user_id: int,
        username: Optional[str],
        first_name: str,
        last_name: str,
        phone: Optional[str] = None
    ) -> UUID:
        """
        Главная функция:
        - находит или создает глобального пользователя
        - связывает platform account
        """
        phone = normalize_phone(phone) if phone else None
        async with self.pool.acquire() as conn:
            async with conn.transaction():

                # 1. Проверяем есть ли уже аккаунт
                account = await conn.fetchrow("""
                    SELECT user_id FROM user_accounts
                    WHERE platform = $1 AND platform_user_id = $2
                """, platform, platform_user_id)

                if account:
                    user_id = account["user_id"]

                    # обновим last_seen
                    await conn.execute("""
                        UPDATE user_accounts
                        SET last_seen = NOW()
                        WHERE platform = $1 AND platform_user_id = $2
                    """, platform, platform_user_id)

                    # если появился телефон — обновим users
                    if phone:
                        # 1. ищем другого пользователя с этим телефоном
                        existing_user = await conn.fetchrow("""
                            SELECT id FROM users WHERE phone_number = $1
                        """, phone)

                        if existing_user and existing_user["id"] != user_id:
                            # MERGE
                            target_user_id = existing_user["id"]

                            # переносим ВСЕ аккаунты
                            await conn.execute("""
                                UPDATE user_accounts
                                SET user_id = $1
                                WHERE user_id = $2
                            """, target_user_id, user_id)

                            # удаляем старого пользователя
                            await conn.execute("""
                                DELETE FROM users WHERE id = $1
                            """, user_id)

                            user_id = target_user_id

                        else:
                            # просто обновляем телефон если его нет
                            await conn.execute("""
                                UPDATE users
                                SET phone_number = COALESCE(phone_number, $1)
                                WHERE id = $2
                            """, phone, user_id)

                    return user_id

                # 2. Нет аккаунта → ищем по телефону
                user_id = None

                if phone:
                    user = await conn.fetchrow("""
                        SELECT id FROM users WHERE phone_number = $1
                    """, phone)

                    if user:
                        user_id = user["id"]

                # 3. Если не нашли — создаем user
                if not user_id:
                    user = await conn.fetchrow("""
                        INSERT INTO users (phone_number)
                        VALUES ($1)
                        ON CONFLICT (phone_number) DO UPDATE
                        SET phone_number = EXCLUDED.phone_number
                        RETURNING id
                    """, phone)

                    user_id = user["id"]

                # 4. Создаем account
                await conn.execute("""
                    INSERT INTO user_accounts (
                        user_id,
                        platform,
                        platform_user_id,
                        username,
                        first_name,
                        last_name,
                        last_seen
                    )
                    VALUES ($1,$2,$3,$4,$5,$6,NOW())
                """,
                    user_id,
                    platform,
                    platform_user_id,
                    username,
                    first_name,
                    last_name
                )

                return user_id