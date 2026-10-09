"""The ranked jobs queue.

Stores the raw score and queued_at; the age decay is computed when
reading; entries expire after 10 days. Served before new fetching on
the next /start.

Location orders the jobs, it never removes them: jobs in your cities
first, then remote, then everywhere else; by score within each group.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core.config import get_config
from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.queries import queue as repo
from jobpilot.models.preferences import Preferences
from jobpilot.reference import normalize

YOUR_CITY, REMOTE, ELSEWHERE = 0, 1, 2


def location_rank(job: dict[str, Any], prefs: Preferences) -> int:
    """Return 0 for a job in your cities, 1 for remote (if wanted), else 2."""
    wanted = {normalize.city_norm(loc) for loc in prefs.locations} - {None}
    if job.get("city") and job["city"] in wanted:
        return YOUR_CITY
    if job.get("remote"):
        return REMOTE if prefs.remote_ok else ELSEWHERE
    return ELSEWHERE


def effective_score(score: float, queued_at: datetime, now: datetime) -> float:
    """Return the score after age decay (a fixed share lost per day queued)."""
    age_days = max(0.0, (now - queued_at).total_seconds() / 86400)
    return round(score * (1 - get_config().queue.age_decay_per_day) ** age_days, 2)


async def enqueue(conn: AsyncConnection, job_id: int, score: float, now: datetime) -> None:
    """Queue (or re-queue) a job with its score and a fresh expiry."""
    await repo.upsert(conn, job_id, score, now, now + timedelta(days=get_config().queue.expiry_days))


async def ranked(
    conn: AsyncConnection, now: datetime, prefs: Preferences | None = None
) -> list[tuple[int, float]]:
    """Return waiting jobs as (job id, decayed score), best first.

    With preferences, jobs are grouped by location rank first, then
    ordered by score.
    """
    rows = await repo.waiting(conn, now)
    scored = [(r["job_id"], effective_score(float(r["score"]), r["queued_at"], now)) for r in rows]
    if prefs is None:
        return sorted(scored, key=lambda x: -x[1])
    jobs = {j["id"]: dict(j) for j in await jobs_repo.by_ids(conn, [job_id for job_id, _ in scored])}
    return sorted(scored, key=lambda x: (location_rank(jobs.get(x[0], {}), prefs), -x[1]))


async def expire(conn: AsyncConnection, now: datetime) -> list[int]:
    """Drop jobs that waited past their expiry; return their ids."""
    return await repo.expire(conn, now)
