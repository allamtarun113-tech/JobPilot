"""SQL for the runs table."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core import clock
from jobpilot.db.tables import runs

# Statuses covered by the one-active-run unique index.
ACTIVE = ("running", "draining")
PAUSED = "paused"


async def get(conn: AsyncConnection, run_id: int) -> RowMapping | None:
    """Return one run, or None."""
    return (await conn.execute(select(runs).where(runs.c.id == run_id))).mappings().one_or_none()


async def get_active(conn: AsyncConnection, *, for_update: bool = False) -> RowMapping | None:
    """Return the running or draining run, if any.

    Args:
        conn: An open connection.
        for_update: Lock the row until the transaction ends.
    """
    stmt = select(runs).where(runs.c.status.in_(ACTIVE))
    if for_update:
        stmt = stmt.with_for_update()
    return (await conn.execute(stmt)).mappings().one_or_none()


async def get_paused(conn: AsyncConnection, *, for_update: bool = False) -> RowMapping | None:
    """Return the newest paused run, if any (optionally row-locked)."""
    stmt = select(runs).where(runs.c.status == PAUSED).order_by(runs.c.id.desc()).limit(1)
    if for_update:
        stmt = stmt.with_for_update()
    return (await conn.execute(stmt)).mappings().one_or_none()


async def latest(conn: AsyncConnection, limit: int = 20) -> list[RowMapping]:
    """Return the newest runs for the Runs tab."""
    rows = await conn.execute(select(runs).order_by(runs.c.id.desc()).limit(limit))
    return list(rows.mappings())


async def create(conn: AsyncConnection, *, holder: str, card_cap: int) -> RowMapping:
    """Start a new run holding the lease.

    Args:
        conn: An open transaction.
        holder: The lease holder id.
        card_cap: The most cards this run may create.

    Returns:
        The new run row.
    """
    now = clock.now()
    row = await conn.execute(
        insert(runs)
        .values(
            status="running",
            lease_holder=holder,
            heartbeat_at=now,
            started_at=now,
            card_cap=card_cap,
            current_step="starting",
        )
        .returning(runs)
    )
    return row.mappings().one()


async def update_held(conn: AsyncConnection, run_id: int, holder: str, **values: Any) -> bool:
    """Update a run only while `holder` still owns its lease."""
    values.setdefault("updated_at", clock.now())
    result = await conn.execute(
        update(runs)
        .where(runs.c.id == run_id, runs.c.lease_holder == holder, runs.c.status.in_(ACTIVE))
        .values(version=runs.c.version + 1, **values)
    )
    return result.rowcount == 1


async def heartbeat(conn: AsyncConnection, holder: str) -> int:
    """Refresh the heartbeat of every active run this holder owns.

    Returns:
        How many runs were updated (0 means the lease was lost).
    """
    now = clock.now()
    result = await conn.execute(
        update(runs).where(runs.c.lease_holder == holder, runs.c.status.in_(ACTIVE)).values(heartbeat_at=now)
    )
    return result.rowcount


def is_stale(run: RowMapping, ttl_seconds: float) -> bool:
    """Return True if the run's heartbeat is older than ``ttl_seconds``."""
    hb = run["heartbeat_at"]
    return hb is None or hb < clock.now() - timedelta(seconds=ttl_seconds)


async def add_usage(conn: AsyncConnection, run_id: int, tokens_in: int, tokens_out: int, cost: float) -> None:
    """Add tokens and cost to a run's running totals."""
    await conn.execute(
        update(runs)
        .where(runs.c.id == run_id)
        .values(
            tokens_in=runs.c.tokens_in + tokens_in,
            tokens_out=runs.c.tokens_out + tokens_out,
            cost_usd=runs.c.cost_usd + cost,
        )
    )


async def mark_paused(conn: AsyncConnection, run_id: int, holder: str, counts: dict[str, Any]) -> bool:
    """Leave the lease (status paused is outside the one-active-run index); the checkpoint keeps the place."""
    return await update_held(
        conn,
        run_id,
        holder,
        status=PAUSED,
        lease_holder=None,
        pause_requested=False,
        current_step="paused",
        counts=counts,
    )


async def resume_paused(conn: AsyncConnection, run_id: int, holder: str) -> bool:
    """Turn a paused run back into a running one held by ``holder``.

    Returns:
        False if the run wasn't paused any more.
    """
    now = clock.now()
    result = await conn.execute(
        update(runs)
        .where(runs.c.id == run_id, runs.c.status == PAUSED)
        .values(
            status="running",
            lease_holder=holder,
            heartbeat_at=now,
            pause_requested=False,
            current_step="resuming",
            version=runs.c.version + 1,
            updated_at=now,
        )
    )
    return result.rowcount == 1


async def finish_paused(conn: AsyncConnection, run_id: int) -> bool:
    """Finish a paused run after /stop.

    Returns:
        False if the run wasn't paused any more.
    """
    now = clock.now()
    result = await conn.execute(
        update(runs)
        .where(runs.c.id == run_id, runs.c.status == PAUSED)
        .values(
            status="finished",
            stop_reason="stop_requested",
            stopped_at=now,
            current_step="done",
            version=runs.c.version + 1,
            updated_at=now,
        )
    )
    return result.rowcount == 1
