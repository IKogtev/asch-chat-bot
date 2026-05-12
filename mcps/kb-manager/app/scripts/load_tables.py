from __future__ import annotations

import argparse
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from app.services.tables_loader_service import TablesLoaderService


DEFAULT_TABLES_DIR = "/app/data/kb_documents/tables"
DEFAULT_DATABASE_URL = "postgresql://aszh-bot:aszh-bot@postgres:5432/nstya_data"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load KB Excel tables into PostgreSQL.")
    parser.add_argument(
        "--tables-dir",
        default=os.getenv("NSTYA_DATA_SOURCE_DIR", DEFAULT_TABLES_DIR),
        help="Directory with Excel table files.",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("NSTYA_DATA_URL", DEFAULT_DATABASE_URL),
        help="PostgreSQL DSN for tables database.",
    )
    parser.add_argument(
        "--strict-validation",
        action="store_true",
        help="Return non-zero exit code when data catalog validation has warnings.",
    )
    return parser.parse_args()


def main() -> int:
    if load_dotenv is not None:
        load_dotenv()
    args = parse_args()
    service = TablesLoaderService(
        database_url=args.database_url,
        tables_dir=Path(args.tables_dir),
    )

    result = service.load_all()
    for table in result.loaded_tables:
        print(
            f"{table.table_name}: {table.rows} rows, {table.columns} columns "
            f"from {table.source_file}/{table.source_sheet}"
        )

    if result.validation_errors:
        print("Validation warnings:")
        for error in result.validation_errors:
            print(f"- {error}")
        if args.strict_validation:
            return 1

    print("Tables load completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
