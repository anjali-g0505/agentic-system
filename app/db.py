"""Async Postgres pool shared by the API and the tools."""
from __future__ import annotations

import logging

from psycopg_pool import AsyncConnectionPool

from app.config import settings

log = logging.getLogger(__name__)

pool = AsyncConnectionPool(conninfo=settings.DATABASE_URL, open=False, min_size=1, max_size=10,
                           kwargs={"autocommit": True})


async def open_pool() -> None:
    try:
        await pool.open(wait=True, timeout=15)
        async with pool.connection() as conn:
            await conn.execute("SELECT 1")
    except Exception as e:  # readable failure at startup instead of a stack trace mid-request
        raise SystemExit(f"STARTUP ERROR: Postgres unreachable via DATABASE_URL ({type(e).__name__}: {e})") from e


async def close_pool() -> None:
    await pool.close()
