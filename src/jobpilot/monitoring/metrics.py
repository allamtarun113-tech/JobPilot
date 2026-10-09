"""Success metrics: recall, approval rate, reply rate - plus this week's cost.

- Recall: jobs you shared that JobPilot already had (hit) / all jobs you shared.
- Approval rate: cards whose resume you approved without any Job Chat edit / cards you approved.
- Reply rate: applications with a reply, interview or offer / applications.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core import clock
from jobpilot.db.queries import shared_jobs
from jobpilot.db.tables import cards, llm_usage, resume_versions

POSITIVE_OUTCOMES = ("replied", "interview", "offer")


@dataclass(frozen=True)
class Rate:
    """A ratio kept as hits and total, so the dashboard can show both."""

    hits: int
    total: int

    @property
    def value(self) -> float | None:
        """The ratio, or None while the total is zero."""
        return self.hits / self.total if self.total else None


async def recall(conn: AsyncConnection, days: int = 30) -> tuple[int, int, float | None]:
    """(hits, shared, recall) over the last `days`. Recall is None until something was shared."""
    hits, total = await shared_jobs.recall_since(conn, clock.now() - timedelta(days=days))
    return hits, total, (hits / total if total else None)


async def approval_rate(conn: AsyncConnection) -> Rate:
    """Return cards approved without any Job Chat edit, out of all approved."""
    approved = select(cards.c.job_id).where(cards.c.approved_resume_version_id.is_not(None))
    total = int(await conn.scalar(select(func.count()).select_from(approved.subquery())) or 0)
    # Jobs with a version made by Job Chat count as edited.
    edited = select(resume_versions.c.job_id).where(resume_versions.c.created_by == "chat").distinct()
    without_edits = int(
        await conn.scalar(
            select(func.count()).select_from(approved.where(cards.c.job_id.not_in(edited)).subquery())
        )
        or 0
    )
    return Rate(without_edits, total)


async def reply_rate(conn: AsyncConnection) -> Rate:
    """Return applications with a reply, interview or offer, out of all."""
    applied = cards.c.applied_at.is_not(None)
    total = int(await conn.scalar(select(func.count()).select_from(cards).where(applied)) or 0)
    replied = int(
        await conn.scalar(
            select(func.count()).select_from(cards).where(applied, cards.c.outcome.in_(POSITIVE_OUTCOMES))
        )
        or 0
    )
    return Rate(replied, total)


async def week_cost(conn: AsyncConnection) -> float:
    """Return the LLM cost of the last seven days in USD."""
    since = clock.now() - timedelta(days=7)
    total = await conn.scalar(
        select(func.coalesce(func.sum(llm_usage.c.cost_usd), 0)).where(llm_usage.c.created_at >= since)
    )
    return float(total or 0)


async def strip(conn: AsyncConnection) -> dict[str, object]:
    """Return every number shown on the dashboard's metrics strip."""
    hits, shared, recall_value = await recall(conn)
    return {
        "recall": recall_value,
        "recall_hits": hits,
        "recall_total": shared,
        "approval": await approval_rate(conn),
        "reply": await reply_rate(conn),
        "week_cost": await week_cost(conn),
    }
