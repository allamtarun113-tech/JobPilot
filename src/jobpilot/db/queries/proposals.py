"""SQL for proposals (Job Chat tier-2 changes awaiting Confirm)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.queries.base import versioned_update
from jobpilot.db.tables import proposals


async def create(conn: AsyncConnection, **values: Any) -> int:
    """Insert a proposal and return its id."""
    return int(
        (await conn.execute(insert(proposals).values(**values).returning(proposals.c.id))).scalar_one()
    )


async def by_token(conn: AsyncConnection, token: str) -> RowMapping | None:
    """Return the proposal whose buttons carry ``token``, or None."""
    return (
        (await conn.execute(select(proposals).where(proposals.c.callback_token == token)))
        .mappings()
        .one_or_none()
    )


async def update_status(conn: AsyncConnection, proposal_id: int, expected_version: int, status: str) -> int:
    """Version-checked status change (confirmed or cancelled)."""
    return await versioned_update(conn, proposals, proposal_id, expected_version, status=status)


async def for_job(conn: AsyncConnection, job_id: int) -> list[RowMapping]:
    """Return a job's proposals, newest first."""
    rows = await conn.execute(
        select(proposals).where(proposals.c.job_id == job_id).order_by(proposals.c.id.desc())
    )
    return list(rows.mappings())
