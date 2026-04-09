"""initial schema (chat, search, subscribers, news)

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-04-02

Replaces former ``ensure_schema()`` DDL in ``bot/services/database.py`` (now commented out).
Apply with ``alembic upgrade head`` before starting the bot.

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a1b2c3d4e5f6"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_user_id", "chat_history", ["user_id"], unique=False)
    op.create_index("idx_created_at", "chat_history", ["created_at"], unique=False)

    op.create_table(
        "search_meta",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("search_id", sa.Text(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column(
            "shown_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("user_id", "session_id"),
    )

    op.create_table(
        "search_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("search_id", sa.Text(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=True),
        sa.Column("score", sa.DOUBLE_PRECISION(), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_search_results_user_session",
        "search_results",
        ["user_id", "session_id", "rank"],
        unique=False,
    )
    op.create_index(
        "idx_search_results_search_id", "search_results", ["search_id"], unique=False
    )

    op.create_table(
        "subscribers",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("first_name", sa.Text(), nullable=True),
        sa.Column("last_name", sa.Text(), nullable=True),
        sa.Column("phone_number", sa.String(length=20), nullable=True),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column(
            "manager_group",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=True,
        ),
        sa.Column(
            "coach_group",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "news",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("files", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'pending'"),
            nullable=True,
        ),
        sa.Column(
            "target_group",
            sa.String(length=50),
            server_default=sa.text("'all'"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("news")
    op.drop_table("subscribers")
    op.drop_index("idx_search_results_search_id", table_name="search_results")
    op.drop_index("idx_search_results_user_session", table_name="search_results")
    op.drop_table("search_results")
    op.drop_table("search_meta")
    op.drop_index("idx_created_at", table_name="chat_history")
    op.drop_index("idx_user_id", table_name="chat_history")
    op.drop_table("chat_history")
