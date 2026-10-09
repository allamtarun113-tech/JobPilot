"""SQL for durable approvals."""

from __future__ import annotations

from sqlalchemy import insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core import clock
from jobpilot.db.tables import approvals


async def create(
    conn: AsyncConnection, *, job_id: int, kind: str, target_id: int, target_version: int
) -> int:
    """Record a durable approval of an exact draft version.

    Args:
        conn: An open transaction.
        job_id: The job the approval is for.
        kind: ``resume`` or ``email``.
        target_id: The approved resume version or email id.
        target_version: The exact version number that was approved.

    Returns:
        The new approval id.
    """
    row = await conn.execute(
        insert(approvals)
        .values(
            job_id=job_id,
            kind=kind,
            target_id=target_id,
            target_version=target_version,
            approved_at=clock.now(),
        )
        .returning(approvals.c.id)
    )
    return int(row.scalar_one())


async def get(conn: AsyncConnection, approval_id: int) -> RowMapping | None:
    """Return one approval row, or None."""
    return (
        (await conn.execute(select(approvals).where(approvals.c.id == approval_id))).mappings().one_or_none()
    )


async def mark_consumed(conn: AsyncConnection, approval_id: int) -> bool:
    """Mark an approval as used, at most once.

    Returns:
        True only for the call that consumed it.
    """
    result = await conn.execute(
        update(approvals)
        # consumed_at IS NULL makes a second consume a no-op.
        .where(approvals.c.id == approval_id, approvals.c.consumed_at.is_(None))
        .values(consumed_at=clock.now(), version=approvals.c.version + 1)
    )
    return result.rowcount == 1
