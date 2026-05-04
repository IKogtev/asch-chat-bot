"""SQLAlchemy metadata for Alembic autogenerate (runtime DB access uses asyncpg)."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass

class ChatHistory(Base):
    __tablename__ = "chat_history"
    __table_args__ = (
        Index("idx_user_id", "user_id"),
        Index("idx_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        nullable=True,
    )

    global_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)

class SearchMeta(Base):
    __tablename__ = "search_meta"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[str] = mapped_column(Text, primary_key=True)
    search_id: Mapped[str] = mapped_column(Text, nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False)
    shown_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa.text("0")
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        nullable=True,
    )


class SearchResult(Base):
    __tablename__ = "search_results"
    __table_args__ = (
        Index("idx_search_results_user_session", "user_id", "session_id", "rank"),
        Index("idx_search_results_search_id", "search_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    search_id: Mapped[str] = mapped_column(Text, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    document_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        nullable=True,
    )


class Subscriber(Base):
    __tablename__ = "subscribers"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )
    region: Mapped[str | None] = mapped_column(Text, nullable=True)
    manager_group: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, server_default=sa.text("false")
    )
    coach_group: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, server_default=sa.text("false")
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True)


class News(Base):
    __tablename__ = "news"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    files: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str | None] = mapped_column(
        String(20), server_default=sa.text("'pending'"), nullable=True
    )
    target_group: Mapped[str | None] = mapped_column(
        String(50), server_default=sa.text("'all'"), nullable=True
    )
    sent_channels: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)    


class LoggedEvent(Base):
    """Telemetry row for ``events`` (kb-manager analytics, ``utils.event_logger``)."""

    __tablename__ = "events"
    __table_args__ = (
        Index("idx_events_user_id", "user_id"),
        Index("idx_events_event_type", "event_type"),
        Index("idx_events_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    global_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)

class UserAccount(Base):
    __tablename__ = "user_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False) # FK на users.id (UUID)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    platform_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=True
    ) 

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(Text, primary_key=True) 
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=True
    )

class UiUser(Base):
    __tablename__ = "ui_users"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)