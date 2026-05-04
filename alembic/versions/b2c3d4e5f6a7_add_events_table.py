"""add events table for telemetry

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-27

Schema matches ``LoggedEvent`` in ``bot/db/models.py`` (formerly ``EventLogger._ensure_table``).
Requires ``pgcrypto`` for ``gen_random_uuid()``.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    op.create_table(
        "events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("user_name", sa.Text(), nullable=True),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_events_user_id", "events", ["user_id"], unique=False)
    op.create_index("idx_events_event_type", "events", ["event_type"], unique=False)
    op.create_index("idx_events_created_at", "events", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_events_created_at", table_name="events")
    op.drop_index("idx_events_event_type", table_name="events")
    op.drop_index("idx_events_user_id", table_name="events")
    op.drop_table("events")
