"""SQL for scores (one row per scoring; the latest per job is current)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.tables import scores


async def add(conn: AsyncConnection, **values: Any) -> int:
    """Insert a score row and return its id."""
    return int((await conn.execute(insert(scores).values(**values).returning(scores.c.id))).scalar_one())


async def latest_for(conn: AsyncConnection, job_id: int) -> RowMapping | None:
    """Return the current (newest) score of a job, or None."""
    row = await conn.execute(
        select(scores).where(scores.c.job_id == job_id).order_by(scores.c.id.desc()).limit(1)
    )
    return row.mappings().one_or_none()


async def latest_for_many(conn: AsyncConnection, job_ids: list[int]) -> dict[int, RowMapping]:
    """Map each job id to its newest score in one query."""
    if not job_ids:
        return {}
    rows = await conn.execute(
        select(scores)
        .where(scores.c.job_id.in_(job_ids))
        .order_by(scores.c.job_id, scores.c.id.desc())
        .ext(distinct_on(scores.c.job_id))
    )
    return {r["job_id"]: r for r in rows.mappings()}


async def history(conn: AsyncConnection, job_id: int, limit: int = 2) -> list[RowMapping]:
    """Newest first."""
    rows = await conn.execute(
        select(scores).where(scores.c.job_id == job_id).order_by(scores.c.id.desc()).limit(limit)
    )
    return list(rows.mappings())
