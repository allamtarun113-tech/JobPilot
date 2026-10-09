"""SQL for api_usage: one row per paid jobs-API call (daily quota and the 80% alert)."""

from __future__ import annotations

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.tables import api_usage


async def record(conn: AsyncConnection, *, provider: str, endpoint: str, run_id: int | None = None) -> None:
    """Log one paid jobs-API call for the daily quota count."""
    await conn.execute(insert(api_usage).values(provider=provider, endpoint=endpoint, run_id=run_id))
