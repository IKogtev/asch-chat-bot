import asyncpg


class AdkDbStatsService:

    def __init__(self, dsn: str):
        self.dsn = dsn

    async def get_stats(self):

        conn = await asyncpg.connect(self.dsn)

        try:

            db_size = await conn.fetchval("""
                SELECT pg_database_size(current_database())
            """)

            connections = await conn.fetchval("""
                SELECT count(*)
                FROM pg_stat_activity
            """)

            tables = await conn.fetch("""
                SELECT
                    schemaname,
                    relname AS table_name,
                    pg_total_relation_size(relid) AS total_bytes,
                    pg_relation_size(relid) AS table_bytes,
                    pg_indexes_size(relid) AS index_bytes,
                    n_live_tup,
                    n_dead_tup
                FROM pg_stat_user_tables
                ORDER BY pg_total_relation_size(relid) DESC
            """)

            return {
                "database_size_bytes": db_size,
                "connections": connections,
                "tables": [dict(row) for row in tables]
            }

        finally:
            await conn.close()