"""web otp_challenges and web_sessions

Revision ID: e8f9a0b1c2d3
Revises: c4d5e6f7a8b9
Create Date: 2026-08-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e8f9a0b1c2d3"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    op.create_table(
        "otp_challenges",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("phone_number", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_otp_challenges_phone_created",
        "otp_challenges",
        ["phone_number", "created_at"],
    )
    op.create_index("idx_otp_challenges_user_id", "otp_challenges", ["user_id"])

    op.create_table(
        "web_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_web_sessions_token_hash", "web_sessions", ["token_hash"], unique=True)
    op.create_index("idx_web_sessions_user_id", "web_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_web_sessions_user_id", table_name="web_sessions")
    op.drop_index("idx_web_sessions_token_hash", table_name="web_sessions")
    op.drop_table("web_sessions")
    op.drop_index("idx_otp_challenges_user_id", table_name="otp_challenges")
    op.drop_index("idx_otp_challenges_phone_created", table_name="otp_challenges")
    op.drop_table("otp_challenges")
