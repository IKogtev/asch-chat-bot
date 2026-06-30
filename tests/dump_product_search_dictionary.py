"""Print rows from local PostgreSQL product_search_dictionary.

Usage:
    python tests/dump_product_search_dictionary.py
    python tests/dump_product_search_dictionary.py --like strategy
    python tests/dump_product_search_dictionary.py --database-url postgresql://user:pass@localhost:5432/nstya_data
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any

import asyncpg


DEFAULT_DATABASE_URL = "postgresql://aszh-bot:aszh-bot@localhost:5432/nstya_data"
PRODUCT_SEARCH_TABLE = "product_search_dictionary"
PRODUCT_SEARCH_COLUMNS = [
    "product_code",
    "canonical_name",
    "alias",
    "normalized_alias",
    "search_tokens",
    "match_type",
    "priority",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dump product_search_dictionary from PostgreSQL.",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("NSTYA_DATA_URL", DEFAULT_DATABASE_URL),
        help=(
            "PostgreSQL URL. Defaults to NSTYA_DATA_URL or "
            f"{DEFAULT_DATABASE_URL!r}."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Maximum number of rows to print. Use 0 for no limit.",
    )
    parser.add_argument(
        "--like",
        default="",
        help=(
            "Case-insensitive substring filter applied to product_code, "
            "canonical_name, alias, normalized_alias, and search_tokens."
        ),
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Print CSV instead of an aligned table.",
    )
    return parser.parse_args()


def _format_cell(value: Any) -> str:
    return "" if value is None else str(value)


def print_table(rows: list[asyncpg.Record]) -> None:
    values = [[_format_cell(row[column]) for column in PRODUCT_SEARCH_COLUMNS] for row in rows]
    widths = [
        max(len(column), *(len(row[index]) for row in values)) if values else len(column)
        for index, column in enumerate(PRODUCT_SEARCH_COLUMNS)
    ]

    header = " | ".join(
        column.ljust(widths[index])
        for index, column in enumerate(PRODUCT_SEARCH_COLUMNS)
    )
    separator = "-+-".join("-" * width for width in widths)
    print(header)
    print(separator)
    for row in values:
        print(
            " | ".join(
                row[index].ljust(widths[index])
                for index in range(len(PRODUCT_SEARCH_COLUMNS))
            )
        )


def print_csv(rows: list[asyncpg.Record]) -> None:
    import csv
    import sys

    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(PRODUCT_SEARCH_COLUMNS)
    for row in rows:
        writer.writerow([_format_cell(row[column]) for column in PRODUCT_SEARCH_COLUMNS])


async def fetch_rows(args: argparse.Namespace) -> list[asyncpg.Record]:
    columns_sql = ", ".join(PRODUCT_SEARCH_COLUMNS)
    limit_sql = "" if args.limit == 0 else f" LIMIT {int(args.limit)}"

    params: list[Any] = []
    where_sql = ""
    if args.like:
        params.append(f"%{args.like}%")
        searchable_columns = [
            "product_code",
            "canonical_name",
            "alias",
            "normalized_alias",
            "search_tokens",
        ]
        where_sql = " WHERE " + " OR ".join(
            f"{column} ILIKE $1"
            for column in searchable_columns
        )

    sql = f"""
        SELECT {columns_sql}
        FROM {PRODUCT_SEARCH_TABLE}
        {where_sql}
        ORDER BY product_code, priority DESC, canonical_name, alias
        {limit_sql}
    """

    conn = await asyncpg.connect(args.database_url)
    try:
        return await conn.fetch(sql, *params)
    finally:
        await conn.close()


async def main() -> None:
    args = parse_args()
    rows = await fetch_rows(args)
    if args.csv:
        print_csv(rows)
    else:
        print_table(rows)
        print(f"\nRows: {len(rows)}")


if __name__ == "__main__":
    asyncio.run(main())
