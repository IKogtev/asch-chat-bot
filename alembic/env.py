import logging
import os
from logging.config import fileConfig
from urllib.parse import urlparse

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

from bot.db.models import Base

load_dotenv(override=True)

logger = logging.getLogger(__name__)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    # url = (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_DSN") or "").strip()
    url = os.getenv("DATABASE_URL")
    if not url:
        # raise RuntimeError(
        #     "DATABASE_URL or POSTGRES_DSN must be set to run Alembic migrations"
        # )
        raise RuntimeError(
            "DATABASE_URL must be set to run Alembic migrations"
        )

    try:
        parsed = urlparse(url)
        safe = f"{parsed.hostname}:{parsed.port}/{parsed.path.lstrip('/')}"
        logger.info("Alembic connecting to database: %s", safe)
    except Exception:
        logger.info("Alembic connecting to database")
    return url


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section)
    if configuration is None:
        raise RuntimeError("Alembic config section [alembic] is missing")
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
