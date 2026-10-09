"""Short row leases (locked_by, locked_until) for long steps on one row.

Taken and released in tiny transactions; never held as a database lock across an LLM or MCP call.
A crashed holder's lease simply expires.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import Table, or_, update
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core import clock


async def take(conn: AsyncConnection, table: Table, row_id: int, holder: str, ttl_seconds: float) -> bool:
    """Take the row's lease if it is free, expired or already ours.

    Args:
        conn: An open connection.
        table: A table with ``locked_by`` / ``locked_until`` columns.
        row_id: The row to lease.
        holder: Who takes the lease (a worker id).
        ttl_seconds: How long the lease lasts unless extended.

    Returns:
        True if this holder now owns the lease.
    """
    now = clock.now()
    result = await conn.execute(
        update(table)
        .where(table.c.id == row_id)
        .where(
            # Free, expired, or re-taken by the same holder
            # (idempotent).
            or_(
                table.c.locked_until.is_(None),
                table.c.locked_until < now,
                table.c.locked_by == holder,
            )
        )
        .values(locked_by=holder, locked_until=now + timedelta(seconds=ttl_seconds))
    )
    return result.rowcount == 1


async def extend(conn: AsyncConnection, table: Table, row_id: int, holder: str, ttl_seconds: float) -> bool:
    """Push back the expiry of a lease this holder still owns.

    Returns:
        False when the lease was lost to someone else.
    """
    result = await conn.execute(
        update(table)
        .where(table.c.id == row_id, table.c.locked_by == holder)
        .values(locked_until=clock.now() + timedelta(seconds=ttl_seconds))
    )
    return result.rowcount == 1


async def release(conn: AsyncConnection, table: Table, row_id: int, holder: str) -> bool:
    """Give the lease back, if this holder still owns it.

    Returns:
        True if the lease was released.
    """
    result = await conn.execute(
        update(table)
        .where(table.c.id == row_id, table.c.locked_by == holder)
        .values(locked_by=None, locked_until=None)
    )
    return result.rowcount == 1
