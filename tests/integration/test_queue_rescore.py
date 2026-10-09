"""A job re-scored below your bar leaves the queue, so it can no longer become a card."""

import pytest

from jobpilot.core import clock
from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.queries import queue as queue_repo
from jobpilot.db.session import transaction
from jobpilot.sources import queue_rank

pytestmark = pytest.mark.usefixtures("db")


async def test_removed_job_is_no_longer_waiting() -> None:
    """A job removed from the queue is no longer waiting."""
    async with transaction() as conn:
        job_id = await jobs_repo.insert_job(
            conn,
            canonical_key="test:queue",
            company="Acme",
            company_norm="acme",
            title="Data Analyst",
            title_norm="data analyst",
            apply_url="https://example.com/apply",
            jd_text="SQL role",
            jd_is_snippet=False,
            pipeline_status="queued",
        )
        await queue_rank.enqueue(conn, job_id, 80, clock.now())
        assert [r["job_id"] for r in await queue_repo.waiting(conn, clock.now())] == [job_id]
        await queue_repo.remove(conn, job_id)
        assert await queue_repo.waiting(conn, clock.now()) == []
