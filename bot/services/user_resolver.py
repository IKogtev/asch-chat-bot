import asyncpg
from typing import Optional
import uuid
from bot.services.utils import normalize_phone
from utils.logger import setup_logger

logger = setup_logger("user_resolver", "bot.log")

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
    ) -> str:
        """
        Главная функция:
        - находит или создает глобального пользователя
        - связывает platform account
        """
        phone = normalize_phone(phone) if phone else None
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # 1. Ищем, привязан ли этот аккаунт платформы к какому-то глобальному пользователю
                # platform_user_id передаем как int (bigint в БД)
                account = await conn.fetchrow("""
                    SELECT user_id FROM user_accounts 
                    WHERE platform = $1 AND platform_user_id = $2
                """, platform, platform_user_id)

                if account:
                    current_global_id = account["user_id"]
                    
                    # Обновляем инфу об аккаунте
                    await conn.execute("""
                        UPDATE user_accounts 
                        SET last_seen = NOW(), username = $3, first_name = $4, last_name = $5
                        WHERE platform = $1 AND platform_user_id = $2
                    """, platform, platform_user_id, username, first_name, last_name)

                    if phone:
                        # Проверяем, нет ли другого пользователя с таким же телефоном
                        other_user = await conn.fetchrow(
                            "SELECT id FROM users WHERE phone_number = $1 AND id != $2", 
                            phone, current_global_id
                        )

                        if other_user:
                            # КРИТИЧЕСКИЙ МОМЕНТ: МЕРДЖ
                            # Найден другой пользователь с этим телефоном. 
                            # Переносим всё на него (он будет главным).
                            target_id = other_user["id"]
                            logger.info(f"MERGE: {current_global_id} -> {target_id} (phone {phone})")

                            # Перепривязываем все аккаунты
                            await conn.execute("UPDATE user_accounts SET user_id = $1 WHERE user_id = $2", target_id, current_global_id)
                            
                            # Перепривязываем историю (в chat_history у вас это global_user_id)
                            await conn.execute("UPDATE chat_history SET global_user_id = $1 WHERE global_user_id = $2", target_id, current_global_id)
                            
                            # Перепривязываем события (после вашего удаления колонки это user_id)
                            await conn.execute("UPDATE events SET user_id = $1 WHERE user_id = $2", target_id, current_global_id)
                            
                            # Перепривязываем результаты поиска (внимание: там тип UUID)
                            try:
                                await conn.execute(
                                    "UPDATE search_results SET user_id = $1::uuid WHERE user_id = $2::uuid", 
                                    target_id, current_global_id
                                )
                                await conn.execute(
                                    "UPDATE search_meta SET user_id = $1::uuid WHERE user_id = $2::uuid", 
                                    target_id, current_global_id
                                )
                            except Exception as e:
                                logger.error(f"Search tables update failed: {e}")

                            # Удаляем старого "пустого" пользователя
                            await conn.execute("DELETE FROM users WHERE id = $1", current_global_id)
                            current_global_id = target_id
                        else:
                            # Если телефона не было — записываем его
                            await conn.execute(
                                "UPDATE users SET phone_number = $1 WHERE id = $2 AND phone_number IS NULL", 
                                phone, current_global_id
                            )
                    
                    return current_global_id

                # 2. Аккаунта нет. Ищем пользователя по телефону.
                user_id = None
                if phone:
                    user_record = await conn.fetchrow("SELECT id FROM users WHERE phone_number = $1", phone)
                    if user_record:
                        user_id = user_record["id"]

                # 3. Если пользователя нет — создаем новый UUID (в формате string для вашей БД)
                if not user_id:
                    new_id = str(uuid.uuid4())
                    await conn.execute("""
                        INSERT INTO users (id, phone_number, created_at, is_blocked)
                        VALUES ($1, $2, NOW(), false)
                    """, new_id, phone)
                    user_id = new_id

                # 4. Создаем запись в user_accounts
                await conn.execute("""
                    INSERT INTO user_accounts (
                        user_id, platform, platform_user_id, username, first_name, last_name, last_seen, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW())
                """, user_id, platform, platform_user_id, username, first_name, last_name)

                return user_id