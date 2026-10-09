"""SQL for events (also the audit log)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.tables import events


async def append(
    conn: AsyncConnection,
    *,
    type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    telegram_update_id: int | None = None,
) -> int | None:
    """Insert an event. Returns its id, or None when the idempotency key was already seen."""
    stmt = (
        insert(events)
        .values(
            type=type,
            payload=payload,
            idempotency_key=idempotency_key,
            telegram_update_id=telegram_update_id,
        )
        .on_conflict_do_nothing()
        .returning(events.c.id)
    )
    return (await conn.execute(stmt)).scalar_one_or_none()


async def get(conn: AsyncConnection, event_id: int) -> RowMapping | None:
    """Return one event row, or None."""
    return (await conn.execute(select(events).where(events.c.id == event_id))).mappings().one_or_none()


async def max_telegram_update_id(conn: AsyncConnection) -> int | None:
    """Return the highest Telegram update id stored, or None.

    The poller resumes long polling from the next id after restart.
    """
    return (await conn.execute(select(func.max(events.c.telegram_update_id)))).scalar_one_or_none()
