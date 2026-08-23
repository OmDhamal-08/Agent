"""
database.py — asyncpg connection pool lifecycle for FastAPI.

Manages the Supabase Postgres connection pool using FastAPI's lifespan
context manager. Provides a `get_db()` dependency for acquiring connections.
"""

import os
import asyncpg
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# Module-level pool reference (set during lifespan)
_pool: asyncpg.Pool | None = None


async def create_pool() -> asyncpg.Pool:
    """Create and return the asyncpg connection pool."""
    global _pool

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "See .env.example for the expected format."
        )

    # Detect Supabase transaction pooler (port 6543) — requires statement_cache_size=0
    is_tx_pooler = ":6543" in DATABASE_URL

    _pool = await asyncpg.create_pool(
        dsn=DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=60,
        max_inactive_connection_lifetime=300.0,
        statement_cache_size=0 if is_tx_pooler else 1024,
    )
    return _pool


async def close_pool():
    """Close the connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    """Return the current pool (must be called after create_pool)."""
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call create_pool() first.")
    return _pool


async def get_db() -> AsyncGenerator[asyncpg.Connection, None]:
    """
    FastAPI dependency that yields a connection from the pool.

    Usage:
        @app.get("/example")
        async def example(conn: asyncpg.Connection = Depends(get_db)):
            ...
    """
    pool = get_pool()
    async with pool.acquire() as connection:
        yield connection
