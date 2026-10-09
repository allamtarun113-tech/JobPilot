"""SQL for prep_sets (one Prepare pack per job)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core import clock
from jobpilot.db.queries.base import versioned_update
from jobpilot.db.tables import prep_sets


async def get(conn: AsyncConnection, job_id: int) -> RowMapping | None:
    """Return a job's prep set, or None."""
    return (
        (await conn.execute(select(prep_sets).where(prep_sets.c.job_id == job_id))).mappings().one_or_none()
    )


async def enqueue(conn: AsyncConnection, job_id: int) -> RowMapping:
    """Mark a job's prep set queued, creating it if needed.

    A second click resets an earlier error and bumps the version.

    Returns:
        The prep set row.
    """
    await conn.execute(
        insert(prep_sets)
        .values(job_id=job_id, status="queued")
        .on_conflict_do_update(
            index_elements=[prep_sets.c.job_id],
            set_={
                "status": "queued",
                "error": None,
                "version": prep_sets.c.version + 1,
                "updated_at": clock.now(),
            },
        )
    )
    row = await get(conn, job_id)
    assert row is not None
    return row


async def update(conn: AsyncConnection, row_id: int, expected_version: int, **values: Any) -> int:
    """Version-checked prep set update.

    Returns:
        The new version.
    """
    return await versioned_update(conn, prep_sets, row_id, expected_version, **values)
