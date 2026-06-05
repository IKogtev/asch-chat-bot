from __future__ import annotations

import argparse
import asyncio
import os
from urllib.parse import urlsplit, urlunsplit

import asyncpg


DEFAULT_ENV_NAME = "ADK_SESSION_SERVICE_URI"
DEFAULT_APP_NAME_ENV_NAME = "ADK_APP_NAME"
DEFAULT_APP_NAME = "agent"
MAINTENANCE_DATABASE = "postgres"


def _normalize_asyncpg_url(database_url: str) -> str:
    parsed = urlsplit(database_url)
    if parsed.scheme == "postgresql+asyncpg":
        return urlunsplit(("postgresql", parsed.netloc, parsed.path, parsed.query, parsed.fragment))
    return database_url


def database_name_from_url(database_url: str) -> str:
    parsed = urlsplit(_normalize_asyncpg_url(database_url))
    return parsed.path.lstrip("/").split("/", 1)[0]


def maintenance_database_url(database_url: str) -> str:
    normalized_url = _normalize_asyncpg_url(database_url)
    parsed = urlsplit(normalized_url)
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            f"/{MAINTENANCE_DATABASE}",
            parsed.query,
            parsed.fragment,
        )
    )


def quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


async def ensure_database_exists(database_url: str) -> bool:
    database_name = database_name_from_url(database_url)
    if not database_name:
        raise ValueError("Database name is missing in ADK session service URI.")
    if database_name == MAINTENANCE_DATABASE:
        raise ValueError("ADK session database must not be the maintenance postgres database.")

    maintenance_url = maintenance_database_url(database_url)
    conn = await asyncpg.connect(maintenance_url)
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            database_name,
        )
        if exists:
            return False
        await conn.execute(f"CREATE DATABASE {quote_ident(database_name)}")
        return True
    finally:
        await conn.close()


async def clear_public_schema(database_url: str) -> None:
    target_url = _normalize_asyncpg_url(database_url)
    conn = await asyncpg.connect(target_url)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        await conn.execute("CREATE SCHEMA public")
    finally:
        await conn.close()


async def prepare_database(database_url: str, *, reset_existing: bool) -> None:
    created = await ensure_database_exists(database_url)
    if reset_existing and not created:
        await clear_public_schema(database_url)


async def ensure_adk_tables(database_url: str, *, app_name: str) -> None:
    from google.adk.sessions.database_session_service import DatabaseSessionService

    service = DatabaseSessionService(db_url=database_url)
    try:
        await service.list_sessions(app_name=app_name)
    finally:
        await service.db_engine.dispose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare PostgreSQL database for Google ADK sessions.")
    parser.add_argument(
        "--database-url",
        default=os.getenv(DEFAULT_ENV_NAME),
        help=f"ADK session PostgreSQL URI. Defaults to ${DEFAULT_ENV_NAME}.",
    )
    parser.add_argument(
        "--app-name",
        default=os.getenv(DEFAULT_APP_NAME_ENV_NAME, DEFAULT_APP_NAME),
        help=f"ADK app name used for schema initialization. Defaults to ${DEFAULT_APP_NAME_ENV_NAME} or {DEFAULT_APP_NAME!r}.",
    )
    parser.add_argument(
        "--reset-existing",
        action="store_true",
        help="Drop and recreate the public schema when the target database already exists.",
    )
    return parser


async def _main_async() -> None:
    args = _build_parser().parse_args()
    if not args.database_url:
        raise SystemExit(f"--database-url or ${DEFAULT_ENV_NAME} must be set.")
    await prepare_database(args.database_url, reset_existing=args.reset_existing)
    await ensure_adk_tables(args.database_url, app_name=args.app_name)


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
