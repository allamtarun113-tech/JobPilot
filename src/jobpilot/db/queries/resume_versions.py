"""Immutable tailored resume versions."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.tables import resume_versions


async def next_version(conn: AsyncConnection, job_id: int) -> int:
    """Return the next resume version number for a job.

    A transaction-scoped advisory lock per job makes two concurrent
    tailors (a run and Job Chat) take different numbers.
    """
    from sqlalchemy import text

    # Held until the transaction ends; 7264011 namespaces this lock.
    await conn.execute(text("SELECT pg_advisory_xact_lock(7264011, :job_id)"), {"job_id": job_id})
    value = await conn.scalar(
        select(func.coalesce(func.max(resume_versions.c.version), 0)).where(
            resume_versions.c.job_id == job_id
        )
    )
    return int(value or 0) + 1


async def create(conn: AsyncConnection, **values: Any) -> int:
    """Insert a resume version and return its id."""
    row = await conn.execute(insert(resume_versions).values(**values).returning(resume_versions.c.id))
    return int(row.scalar_one())


async def get(conn: AsyncConnection, version_id: int) -> RowMapping | None:
    """Return one resume version, or None."""
    return (
        (await conn.execute(select(resume_versions).where(resume_versions.c.id == version_id)))
        .mappings()
        .one_or_none()
    )


async def latest(conn: AsyncConnection, job_id: int) -> RowMapping | None:
    """Return a job's newest resume version, or None."""
    return (
        (
            await conn.execute(
                select(resume_versions)
                .where(resume_versions.c.job_id == job_id)
                .order_by(resume_versions.c.version.desc())
                .limit(1)
            )
        )
        .mappings()
        .one_or_none()
    )


async def list_for_job(conn: AsyncConnection, job_id: int) -> list[RowMapping]:
    """Return every resume version of a job, newest first."""
    rows = await conn.execute(
        select(resume_versions)
        .where(resume_versions.c.job_id == job_id)
        .order_by(resume_versions.c.version.desc())
    )
    return list(rows.mappings())


async def latest_for_many(conn: AsyncConnection, job_ids: list[int]) -> dict[int, RowMapping]:
    """The newest resume version of each job (one query, for the jobs table)."""
    if not job_ids:
        return {}
    stmt = (
        select(resume_versions)
        .where(resume_versions.c.job_id.in_(job_ids))
        .order_by(resume_versions.c.job_id, resume_versions.c.version.desc())
        .distinct(resume_versions.c.job_id)
    )
    return {int(r["job_id"]): r for r in (await conn.execute(stmt)).mappings()}
