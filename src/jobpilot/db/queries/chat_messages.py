"""SQL for chat_messages (the Job Chat history per job)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.tables import chat_messages


async def add(conn: AsyncConnection, **values: Any) -> int:
    """Insert one chat message and return its id."""
    return int(
        (
            await conn.execute(insert(chat_messages).values(**values).returning(chat_messages.c.id))
        ).scalar_one()
    )


async def for_job(conn: AsyncConnection, job_id: int, limit: int = 30) -> list[RowMapping]:
    """Return a job's last ``limit`` chat messages, oldest first."""
    rows = await conn.execute(
        select(chat_messages)
        .where(chat_messages.c.job_id == job_id)
        .order_by(chat_messages.c.id.desc())
        .limit(limit)
    )
    # Fetched newest first for the LIMIT, then put back in time order.
    return list(reversed(list(rows.mappings())))


async def latest(conn: AsyncConnection) -> RowMapping | None:
    """Return the newest chat message of any job, or None."""
    return (
        (await conn.execute(select(chat_messages).order_by(chat_messages.c.id.desc()).limit(1)))
        .mappings()
        .one_or_none()
    )
