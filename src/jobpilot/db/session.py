"""Async engine and short transactions. Never hold a transaction across an LLM or MCP call."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from jobpilot.core.config import get_settings

# One engine per process, created on first use.
_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first use.

    On Vercel it connects through the transaction pooler with no client
    pool; elsewhere it keeps a small long-lived pool.
    """
    global _engine
    if _engine is None:
        settings = get_settings()
        if settings.serverless:
            # Serverless + Supabase transaction pooler: no client-side pool, no prepared statements.
            _engine = create_async_engine(
                settings.database_pooler_url or settings.database_url,
                poolclass=NullPool,
                connect_args={"prepare_threshold": None},
            )
        else:
            # No pre-ping: it costs a full round trip per transaction (~0.3 s to a far-away database). A new
            # connection to the cloud pooler costs seconds (TLS + auth), so connections live 30 minutes; one
            # that died meanwhile (a laptop waking up) fails once, invalidates the pool, and the step retries.
            _engine = create_async_engine(settings.database_url, pool_recycle=1800, pool_size=10)
    return _engine


async def dispose_engine() -> None:
    """Close every pooled connection and forget the engine."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncConnection]:
    """One short transaction: commit on success, roll back on any error."""
    async with get_engine().begin() as conn:
        yield conn


@asynccontextmanager
async def read() -> AsyncIterator[AsyncConnection]:
    """Run read-only statements without BEGIN/COMMIT.

    This costs one network round trip per statement instead of three.
    Each statement sees its own snapshot, so use ``transaction()``
    whenever reads must agree or anything is written.

    Yields:
        An autocommit connection.
    """
    async with get_engine().connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        yield conn
