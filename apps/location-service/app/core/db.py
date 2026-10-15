"""Postgres: connection pools and a small migration runner (psycopg 3).

DATABASE_URL   postgresql://user:password@host:5432/db   (required)
DB_POOL_MAX    10                                        connections per pod (topic 17: pods × this ≤ max_connections)
"""

import logging

import psycopg
from psycopg_pool import AsyncConnectionPool, ConnectionPool

from .config import env_int, env_str

log = logging.getLogger("db")

MIGRATION_LOCK = 4_242_001  # pg_advisory_lock key: two migration Jobs never run at the same time


def async_pool() -> AsyncConnectionPool:
    """Opened by the caller with `await pool.open(wait=False)`: the app starts even if Postgres isn't up yet."""
    return AsyncConnectionPool(
        env_str("DATABASE_URL"), min_size=1, max_size=env_int("DB_POOL_MAX", 10), open=False, timeout=5
    )


def sync_pool() -> ConnectionPool:
    return ConnectionPool(
        env_str("DATABASE_URL"), min_size=1, max_size=env_int("DB_POOL_MAX", 10), open=False, timeout=5
    )


def async_check(pool: AsyncConnectionPool):
    async def check() -> None:
        async with pool.connection(timeout=2) as conn:
            await conn.execute("SELECT 1")

    return check


def sync_check(pool: ConnectionPool):
    def check() -> None:
        with pool.connection(timeout=2) as conn:
            conn.execute("SELECT 1")

    return check


def migrate(migrations: list[tuple[int, str, str]]) -> int:
    """Apply (version, name, sql) migrations that haven't run yet, in order. Returns how many ran."""
    applied = 0
    with psycopg.connect(env_str("DATABASE_URL"), autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version integer PRIMARY KEY, name text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
            for version, name, sql in sorted(migrations):
                if version in done:
                    continue
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute("INSERT INTO schema_migrations (version, name) VALUES (%s, %s)", (version, name))
                # not "name": that's a reserved LogRecord attribute, and logging raises KeyError on it
                log.info("migration applied", extra={"version": version, "migration": name})
                applied += 1
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK,))
    log.info("migrations up to date", extra={"applied_now": applied})
    return applied
