from __future__ import annotations

import argparse
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from utils.logger import setup_logger
from app.services.tables_loader_service import GLOSSARY_TABLE_NAME, TablesLoaderService


DEFAULT_TABLES_DIR = "/app/data/kb_documents/manager/tables"
DEFAULT_GLOSSARY_DIR = "/app/data/kb_documents/manager/glossary"
DEFAULT_DATABASE_URL = "postgresql://aszh-bot:aszh-bot@postgres:5432/nstya_data"
logger = setup_logger("load_tables", log_file="load_tables.log")

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
        "--glossary-dir",
        default=os.getenv("GLOSSARY_SOURCE_DIR", DEFAULT_GLOSSARY_DIR),
        help="Directory with glossary Excel files.",
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
        glossary_dir=Path(args.glossary_dir),
    )

    result = service.load_all()
    for table in result.loaded_tables:
        print(
            f"{table.table_name}: {table.rows} rows, {table.columns} columns "
            f"from {table.source_file}/{table.source_sheet}"
        )
        if table.table_name == GLOSSARY_TABLE_NAME:
            print(f"Glossary terms loaded: {table.rows}")
        

    if result.product_kit_products_total is not None:
        active_found = (
            result.product_kit_folders_found or 0
        )
        archive_found = (
            result.archive_product_kit_folders_found or 0
        )
        total_found = (
            active_found
            + archive_found
        )
        print(
            "Product kit folders found: "
            f"{active_found} active of "
            f"{result.product_kit_products_total} products."
        )
        print(
            "Archive product kit folders found: "
            f"{archive_found}."
        )
        print(
            "Total product kit folders resolved: "
            f"{total_found} of "
            f"{result.product_kit_products_total} products."
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
