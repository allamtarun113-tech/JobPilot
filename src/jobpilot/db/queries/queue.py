"""SQL for the ranked jobs queue."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.tables import queue


async def upsert(
    conn: AsyncConnection, job_id: int, score: float, queued_at: datetime, expires_at: datetime
) -> None:
    """Queue a job, or re-queue it after a re-score.

    A re-queued job gets the new score, a fresh queue time and expiry,
    and is marked not yet served; otherwise it would never be served
    again.
    """
    stmt = insert(queue).values(job_id=job_id, score=score, queued_at=queued_at, expires_at=expires_at)
    await conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[queue.c.job_id],
            set_={"score": score, "queued_at": queued_at, "expires_at": expires_at, "served_at": None},
        )
    )


async def waiting(conn: AsyncConnection, now: datetime) -> list[RowMapping]:
    """Return queued jobs not yet served and not expired."""
    rows = await conn.execute(select(queue).where(queue.c.served_at.is_(None), queue.c.expires_at > now))
    return list(rows.mappings())


async def remove(conn: AsyncConnection, job_id: int) -> None:
    """Take a job that is still waiting out of the queue (it was re-scored below your bar)."""
    await conn.execute(delete(queue).where(queue.c.job_id == job_id, queue.c.served_at.is_(None)))


async def mark_served(conn: AsyncConnection, job_id: int, now: datetime) -> None:
    """Mark a queued job as served (it got a card)."""
    await conn.execute(update(queue).where(queue.c.job_id == job_id).values(served_at=now))


async def expire(conn: AsyncConnection, now: datetime) -> list[int]:
    """Drop unserved jobs past their expiry.

    Returns:
        The ids of the expired jobs.
    """
    rows = await conn.execute(
        delete(queue).where(queue.c.served_at.is_(None), queue.c.expires_at <= now).returning(queue.c.job_id)
    )
    return [r[0] for r in rows.all()]
