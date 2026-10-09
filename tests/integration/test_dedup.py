"""Layered dedup across sources: a repeat becomes a sighting, never a second job."""

import pytest
from sqlalchemy import func, select

from jobpilot.db.session import transaction
from jobpilot.db.tables import job_sources, jobs
from jobpilot.models.job import JobPosting, SourceKind
from jobpilot.sources import dedup
from tests.conftest import JD

pytestmark = pytest.mark.usefixtures("db")


def posting(**kw: object) -> JobPosting:
    """Build a posting with test defaults, overridden by ``kw``."""
    base: dict[str, object] = dict(
        source=SourceKind.SEARCH,
        external_id="x",
        title="Data Analyst",
        company="Acme",
        location="Bengaluru",
        url="https://example.com/1",
    )
    return JobPosting.model_validate(base | kw)


async def counts() -> tuple[int, int]:
    """Return (jobs, sightings) row counts."""
    async with transaction() as conn:
        n_jobs = (await conn.execute(select(func.count()).select_from(jobs))).scalar_one()
        n_sightings = (await conn.execute(select(func.count()).select_from(job_sources))).scalar_one()
    return n_jobs, n_sightings


async def test_layered_dedup() -> None:
    """Each dedup layer merges its repeat into one job, and a full JD upgrades the snippet."""
    li = posting(
        external_id="linkedin:3912345678",
        url="https://www.linkedin.com/comm/jobs/view/3912345678/",
        company="Acme Analytics Private Limited",
        location="Bengaluru, Karnataka, India",
    )
    api = posting(
        source=SourceKind.API,
        external_id="jsearch:1",
        url="https://www.linkedin.com/jobs/view/3912345678/",
        company="ACME Analytics",
        description=JD,
        description_is_snippet=False,
    )
    indeed = posting(
        external_id="indeed:1a2b3c4d5e6f7a8b",
        url="https://in.indeed.com/rc/clk?jk=1a2b3c4d5e6f7a8b",
        title="Data Analyst (SQL, Tableau)",
        company="Acme Analytics",
        location="Bangalore",
    )
    repost = posting(
        source=SourceKind.API,
        external_id="jsearch:2",
        url="https://acme.example/careers/99",
        title="Analyst, Retail Insights",
        company="Acme Analytics",
        description=JD.replace("Snowflake", "BigQuery"),
        description_is_snippet=False,
    )
    other = posting(
        external_id="linkedin:3923456789",
        url="https://www.linkedin.com/comm/jobs/view/3923456789/",
        title="Junior Data Scientist",
        company="Nimbus Labs",
        location="Hyderabad",
    )
    async with transaction() as conn:
        results = await dedup.ingest(conn, [li, api, indeed, repost, other], run_id=None)
        again = await dedup.ingest(conn, [li], run_id=None)
    assert [r.new_job for r in results] == [True, False, False, False, True]
    assert [r.layer for r in results] == ["", "board_id", "company_title_city", "near_duplicate_jd", ""]
    assert again[0].new_sighting is False
    assert await counts() == (2, 5)
    async with transaction() as conn:
        first = (await conn.execute(select(jobs).where(jobs.c.id == results[0].job_id))).mappings().one()
    assert (
        first["jd_is_snippet"] is False and "retail insights" in first["jd_text"]
    )  # upgraded by the API's full JD
