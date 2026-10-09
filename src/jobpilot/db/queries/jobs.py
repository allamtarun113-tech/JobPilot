"""SQL for jobs and job_sources (sightings)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.queries.base import versioned_update
from jobpilot.db.tables import job_sources, jobs


async def get(conn: AsyncConnection, job_id: int) -> RowMapping | None:
    """Return one job row, or None."""
    return (await conn.execute(select(jobs).where(jobs.c.id == job_id))).mappings().one_or_none()


async def by_ids(conn: AsyncConnection, ids: list[int]) -> list[RowMapping]:
    """Return jobs in the order of ``ids``, skipping missing ones."""
    if not ids:
        return []
    rows = await conn.execute(select(jobs).where(jobs.c.id.in_(ids)))
    by_id = {r["id"]: r for r in rows.mappings()}
    return [by_id[i] for i in ids if i in by_id]


async def by_canonical_key(conn: AsyncConnection, key: str) -> RowMapping | None:
    """Return the job with this board key (dedup layer 1), or None."""
    return (await conn.execute(select(jobs).where(jobs.c.canonical_key == key))).mappings().one_or_none()


async def by_company_title(conn: AsyncConnection, company_norm: str, title_norm: str) -> list[RowMapping]:
    """Return jobs with the same company and title (dedup layer 2)."""
    rows = await conn.execute(
        select(jobs)
        .where(jobs.c.company_norm == company_norm, jobs.c.title_norm == title_norm)
        .order_by(jobs.c.id)
    )
    return list(rows.mappings())


async def with_simhash_at_company(conn: AsyncConnection, company_norm: str) -> list[RowMapping]:
    """Return a company's jobs that have a JD fingerprint (dedup layer 3)."""
    rows = await conn.execute(
        select(jobs)
        .where(jobs.c.company_norm == company_norm, jobs.c.jd_simhash.is_not(None))
        .order_by(jobs.c.id)
    )
    return list(rows.mappings())


async def insert_job(conn: AsyncConnection, **values: Any) -> int:
    """Insert a job and return its id."""
    return int((await conn.execute(insert(jobs).values(**values).returning(jobs.c.id))).scalar_one())


async def insert_job_once(conn: AsyncConnection, **values: Any) -> int:
    """Insert a job, or return the id of the one already stored.

    A concurrent writer (a run and a shared link) may insert the same
    canonical key first. ON CONFLICT DO NOTHING avoids a unique
    violation that would abort the whole transaction.

    Returns:
        The id of the new or existing job.
    """
    stmt = (
        pg_insert(jobs)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[jobs.c.canonical_key])
        .returning(jobs.c.id)
    )
    new_id = (await conn.execute(stmt)).scalar_one_or_none()
    if new_id is not None:
        return int(new_id)
    # Someone else inserted it first: read their row back.
    existing = await by_canonical_key(conn, str(values["canonical_key"]))
    assert existing is not None
    return int(existing["id"])


async def update(conn: AsyncConnection, job_id: int, expected_version: int, **values: Any) -> int:
    """Version-checked job update.

    Returns:
        The new version.
    """
    return await versioned_update(conn, jobs, job_id, expected_version, **values)


async def add_sighting(
    conn: AsyncConnection,
    *,
    job_id: int,
    source: str,
    external_id: str,
    url: str | None,
    posted_at: datetime | None,
    run_id: int | None,
    snippet: dict[str, Any],
) -> bool:
    """Record a sighting. False if this exact sighting (source + external id) was already stored."""
    stmt = (
        pg_insert(job_sources)
        .values(
            job_id=job_id,
            source=source,
            external_id=external_id,
            url=url,
            posted_at=posted_at,
            run_id=run_id,
            snippet=snippet,
        )
        .on_conflict_do_nothing(index_elements=[job_sources.c.source, job_sources.c.external_id])
        .returning(job_sources.c.id)
    )
    return (await conn.execute(stmt)).scalar_one_or_none() is not None


async def sources_for(conn: AsyncConnection, job_id: int) -> list[RowMapping]:
    """Return every sighting of a job, oldest first."""
    rows = await conn.execute(
        select(job_sources).where(job_sources.c.job_id == job_id).order_by(job_sources.c.id)
    )
    return list(rows.mappings())


async def source_counts(conn: AsyncConnection, job_ids: list[int]) -> dict[int, list[str]]:
    """Map each job id to the sorted, distinct sources that saw it."""
    if not job_ids:
        return {}
    rows = await conn.execute(
        select(job_sources.c.job_id, func.array_agg(func.distinct(job_sources.c.source)))
        .where(job_sources.c.job_id.in_(job_ids))
        .group_by(job_sources.c.job_id)
    )
    return {job_id: sorted(srcs) for job_id, srcs in rows.all()}


async def by_status(conn: AsyncConnection, statuses: tuple[str, ...], limit: int = 500) -> list[RowMapping]:
    """Return jobs in any of the given pipeline statuses, oldest first."""
    rows = await conn.execute(
        select(jobs).where(jobs.c.pipeline_status.in_(statuses)).order_by(jobs.c.id).limit(limit)
    )
    return list(rows.mappings())


async def latest(conn: AsyncConnection, *, status: str | None = None, limit: int = 200) -> list[RowMapping]:
    """Return the newest jobs, optionally only one pipeline status."""
    stmt = select(jobs).order_by(jobs.c.id.desc()).limit(limit)
    if status:
        stmt = stmt.where(jobs.c.pipeline_status == status)
    return list((await conn.execute(stmt)).mappings())


async def status_counts(conn: AsyncConnection) -> dict[str, int]:
    """Return how many jobs are in each pipeline status."""
    rows = await conn.execute(select(jobs.c.pipeline_status, func.count()).group_by(jobs.c.pipeline_status))
    return {s: n for s, n in rows.all()}
