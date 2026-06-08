"""migrate users from sub

Revision ID: a7f2k9x4b1m3
Revises: 2230067e5d57
Create Date: 2026-06-08

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7f2k9x4b1m3'
down_revision = '2230067e5d57'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # --------------------------------------------------
    # 1. Старым пользователям ставим telegram
    # --------------------------------------------------

    conn.execute(sa.text("""
        UPDATE subscribers
        SET platform = 'telegram'
        WHERE platform IS NULL;
    """))

    # --------------------------------------------------
    # 2. Нормализуем телефоны в users
    # --------------------------------------------------

    conn.execute(sa.text("""
        UPDATE users
        SET phone_number =
            CASE
                WHEN phone_number LIKE '+%' THEN phone_number
                ELSE '+' || phone_number
            END
        WHERE
            phone_number IS NOT NULL
            AND trim(phone_number) <> '';
    """))

    # --------------------------------------------------
    # 3. Создаем отсутствующих users
    # --------------------------------------------------

    conn.execute(sa.text("""
        INSERT INTO users (
            id,
            phone_number,
            created_at,
            is_blocked
        )
        SELECT
            gen_random_uuid()::text,
            CASE
                WHEN s.phone_number LIKE '+%' THEN s.phone_number
                ELSE '+' || s.phone_number
            END,
            MIN(s.created_at)::timestamp,
            FALSE
        FROM subscribers s
        LEFT JOIN users u
            ON u.phone_number =
                CASE
                    WHEN s.phone_number LIKE '+%' THEN s.phone_number
                    ELSE '+' || s.phone_number
                END
        WHERE
            s.phone_number IS NOT NULL
            AND trim(s.phone_number) <> ''
            AND u.id IS NULL
        GROUP BY
            CASE
                WHEN s.phone_number LIKE '+%' THEN s.phone_number
                ELSE '+' || s.phone_number
            END;
    """))

    # --------------------------------------------------
    # 4. Создаем user_accounts
    # --------------------------------------------------

    conn.execute(sa.text("""
        INSERT INTO user_accounts (
            user_id,
            platform,
            platform_user_id,
            username,
            first_name,
            last_name,
            last_seen,
            created_at
        )
        SELECT
            u.id,
            COALESCE(s.platform, 'telegram'),
            s.user_id,
            s.username,
            s.first_name,
            s.last_name,
            s.last_seen::timestamp,
            s.created_at::timestamp
        FROM subscribers s
        JOIN users u
            ON u.phone_number =
                CASE
                    WHEN s.phone_number LIKE '+%' THEN s.phone_number
                    ELSE '+' || s.phone_number
                END
        LEFT JOIN user_accounts ua
            ON ua.platform_user_id = s.user_id
            AND ua.platform = COALESCE(s.platform, 'telegram')
        WHERE ua.id IS NULL;
    """))

    # --------------------------------------------------
    # 5. Заполняем chat_history.global_user_id
    # --------------------------------------------------

    conn.execute(sa.text("""
        UPDATE chat_history ch
        SET global_user_id = ua.user_id
        FROM user_accounts ua
        WHERE
            ch.user_id = ua.platform_user_id
            AND (
                ch.global_user_id IS NULL
                OR ch.global_user_id = ''
            );
    """))

    # --------------------------------------------------
    # 6. Переводим events.user_id на global uuid
    # --------------------------------------------------

    conn.execute(sa.text("""
        UPDATE events e
        SET user_id = ua.user_id
        FROM user_accounts ua
        WHERE e.user_id = ua.platform_user_id::text;
    """))


def downgrade():
    pass