"""add channel to chat_history for per-channel reset and context

Revision ID: c4d5e6f7a8b9
Revises: a7f2k9x4b1m3
Create Date: 2026-08-25
"""

from alembic import op
import sqlalchemy as sa

revision = "c4d5e6f7a8b9"
down_revision = "a7f2k9x4b1m3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_history", sa.Column("channel", sa.Text(), nullable=True))
    op.create_index(
        "idx_chat_history_global_user_channel",
        "chat_history",
        ["global_user_id", "channel"],
    )


def downgrade() -> None:
    op.drop_index("idx_chat_history_global_user_channel", table_name="chat_history")
    op.drop_column("chat_history", "channel")
