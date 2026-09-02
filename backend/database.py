"""asyncpg connection pool lifecycle for FastAPI."""

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
    # Lightweight, idempotent runtime migrations.  This lets existing demo
    # databases adopt security and payment-integrity changes without a reset.
    async with _pool.acquire() as connection:
        await connection.execute(
            "ALTER TABLE customer_identities "
            "ADD COLUMN IF NOT EXISTS recovery_code_hash TEXT"
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS order_items (
                id SERIAL PRIMARY KEY,
                order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                product_id INTEGER NOT NULL REFERENCES products(id),
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                source VARCHAR(20) NOT NULL
                    CHECK (source IN ('ai_recommendation', 'ai_upsell', 'organic')),
                unit_price NUMERIC(10, 2) NOT NULL CHECK (unit_price >= 0)
            )
            """
        )
        await connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id)"
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
    """FastAPI dependency that yields a database connection.

    Uses the pool when available (local dev with uvicorn).
    Falls back to a direct connection on serverless cold starts (Vercel).
    """
    if _pool is not None:
        async with _pool.acquire() as connection:
            yield connection
    else:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL environment variable is not set.")
        conn = await asyncpg.connect(dsn=DATABASE_URL, statement_cache_size=0)
        try:
            yield conn
        finally:
            await conn.close()
