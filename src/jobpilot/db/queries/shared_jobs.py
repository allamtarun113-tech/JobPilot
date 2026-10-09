"""SQL for shared_jobs (recall measurement)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.tables import shared_jobs


async def add(conn: AsyncConnection, *, job_id: int, event_id: int, url: str, hit: bool) -> None:
    """Record a job the user shared and whether it was already known."""
    await conn.execute(insert(shared_jobs).values(job_id=job_id, event_id=event_id, url=url, hit=hit))


async def recall_since(conn: AsyncConnection, since: datetime) -> tuple[int, int]:
    """(hits, total) of shared jobs since a time."""
    row = await conn.execute(
        select(func.count().filter(shared_jobs.c.hit), func.count()).where(shared_jobs.c.created_at >= since)
    )
    hits, total = row.one()
    return int(hits), int(total)
