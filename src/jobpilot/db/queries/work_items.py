"""SQL for work_items (the worker's Postgres queue)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core import clock
from jobpilot.db.tables import work_items


async def enqueue(
    conn: AsyncConnection, *, kind: str, payload: dict[str, Any], idempotency_key: str | None
) -> int | None:
    """Add a work item unless its idempotency key exists.

    Args:
        conn: An open transaction.
        kind: The work kind (see ``models/work.py``).
        payload: JSON arguments for the handler.
        idempotency_key: Deduplicates repeats; None allows any number.

    Returns:
        The new item id, or None for a duplicate.
    """
    stmt = (
        insert(work_items)
        .values(kind=kind, payload=payload, idempotency_key=idempotency_key, run_after=clock.now())
        .on_conflict_do_nothing()
        .returning(work_items.c.id)
    )
    return (await conn.execute(stmt)).scalar_one_or_none()


async def claim(conn: AsyncConnection, *, holder: str, limit: int, ttl_seconds: float) -> list[RowMapping]:
    """Claim ready items (or items whose holder's lease expired) with FOR UPDATE SKIP LOCKED."""
    now = clock.now()
    ready = (
        select(work_items.c.id)
        .where(
            or_(
                # Ready items, plus running items whose worker lease
                # expired
                # (the worker crashed).
                (work_items.c.status == "queued") & (work_items.c.run_after <= now),
                (work_items.c.status == "running") & (work_items.c.locked_until < now),
            )
        )
        .order_by(work_items.c.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    rows = await conn.execute(
        update(work_items)
        .where(work_items.c.id.in_(ready))
        .values(
            status="running",
            locked_by=holder,
            locked_until=now + timedelta(seconds=ttl_seconds),
            attempts=work_items.c.attempts + 1,
            updated_at=now,
        )
        .returning(work_items.c.id, work_items.c.kind, work_items.c.payload, work_items.c.attempts)
    )
    return list(rows.mappings())


async def finish(conn: AsyncConnection, item_id: int, holder: str) -> None:
    """Mark an item done, if this holder still owns it."""
    now = clock.now()
    await conn.execute(
        update(work_items)
        .where(work_items.c.id == item_id, work_items.c.locked_by == holder)
        .values(status="done", finished_at=now, locked_by=None, locked_until=None, updated_at=now)
    )


async def fail(
    conn: AsyncConnection, item_id: int, holder: str, *, error: str, retry_in_seconds: float | None
) -> None:
    """Retry later (retry_in_seconds) or give up (None)."""
    now = clock.now()
    values: dict[str, Any] = {
        "last_error": error[:2000],
        "locked_by": None,
        "locked_until": None,
        "updated_at": now,
    }
    if retry_in_seconds is None:
        values.update(status="failed", finished_at=now)
    else:
        values.update(status="queued", run_after=now + timedelta(seconds=retry_in_seconds))
    await conn.execute(
        update(work_items)
        .where(work_items.c.id == item_id, work_items.c.locked_by == holder)
        .values(**values)
    )


async def extend_held(conn: AsyncConnection, holder: str, ttl_seconds: float) -> int:
    """Extend the lease of every item this holder is running.

    Returns:
        How many items were extended.
    """
    result = await conn.execute(
        update(work_items)
        .where(work_items.c.locked_by == holder, work_items.c.status == "running")
        .values(locked_until=clock.now() + timedelta(seconds=ttl_seconds))
    )
    return result.rowcount


async def count_by_status(conn: AsyncConnection) -> dict[str, int]:
    """Return how many work items are in each status."""
    rows = await conn.execute(select(work_items.c.status, func.count()).group_by(work_items.c.status))
    return {status: n for status, n in rows.all()}
