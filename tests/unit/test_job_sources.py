"""Normalization, board ids, SimHash, hard filters, queue decay, shares."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jobpilot.matching import hard_filters
from jobpilot.models.preferences import Preferences
from jobpilot.reference import normalize
from jobpilot.sources.queue_rank import ELSEWHERE, REMOTE, YOUR_CITY, effective_score, location_rank
from jobpilot.sources.shared_links import posting_from_share
from tests.conftest import JD

ALERTS = Path(__file__).parent.parent / "fixtures" / "alerts"


def test_company_title_city_normalization() -> None:
    """Company, title and city names normalize as expected."""
    assert normalize.company_norm("Acme Analytics Private Limited") == "acme analytics"
    assert normalize.company_norm("Zenith Softech Pvt. Ltd.") == "zenith softech"
    assert normalize.company_norm("Microsoft India (R&D) Pvt Ltd") == "microsoft"
    assert normalize.title_norm("Sr. Python Dev - Bengaluru (Remote)") == "senior python developer"
    assert normalize.title_norm("SDE II") == "software development engineer 2"
    assert normalize.city_norm("Bangalore Urban, Karnataka, India") == "bengaluru"
    assert normalize.city_norm("Gurgaon, Haryana") == "gurugram"
    assert normalize.city_norm("Remote") is None and normalize.city_norm("India") is None
    assert normalize.is_remote("Pune, Remote") and not normalize.is_remote("Pune")


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.linkedin.com/comm/jobs/view/3912345678/?trackingId=abc", ("linkedin", "3912345678")),
        ("https://www.linkedin.com/jobs/view/data-analyst-at-acme-3912345678", ("linkedin", "3912345678")),
        (
            "https://www.linkedin.com/jobs/search/?currentJobId=3912345678&keywords=x",
            ("linkedin", "3912345678"),
        ),
        (
            "https://www.naukri.com/job-listings-python-developer-zenith-bengaluru-1-to-3-years-021026500123?src=a",
            ("naukri", "021026500123"),
        ),
        ("https://in.indeed.com/rc/clk?jk=1a2b3c4d5e6f7a8b&from=ja", ("indeed", "1a2b3c4d5e6f7a8b")),
        ("https://boards.greenhouse.io/acme/jobs/4012345", ("greenhouse", "acme:4012345")),
        (
            "https://click.example.com/r?url=https%3A%2F%2Fwww.linkedin.com%2Fcomm%2Fjobs%2Fview%2F3999999999%2F",
            ("linkedin", "3999999999"),
        ),
        ("https://www.linkedin.com/comm/jobs/search?keywords=data", None),
    ],
)
def test_board_job_id(url: str, expected: tuple[str, str] | None) -> None:
    """Board job ids are found in links, including tracking redirects."""
    assert normalize.board_job_id(url) == expected


def test_simhash_merges_reposts_but_not_different_roles() -> None:
    """A repost is a near-duplicate; a different role is not."""
    repost = JD.replace("Bengaluru", "Bangalore").replace("Snowflake", "BigQuery")
    other_role = JD.replace("Data Analyst", "Data Engineer").replace(
        "dashboards in Tableau and Power BI", "pipelines in Spark and Kafka"
    ) + (" Build streaming ingestion with Kafka Connect and Flink; own data quality SLAs and on-call.")
    base = normalize.simhash(JD)
    assert normalize.hamming(base, normalize.simhash(repost)) <= 6
    assert normalize.hamming(base, normalize.simhash(other_role)) > 6


def job(**kw: object) -> dict[str, object]:
    """Return a job dict with test defaults, overridden by ``kw``."""
    base = {
        "company_norm": "acme",
        "title_norm": "data analyst",
        "city": "bengaluru",
        "remote": False,
        "experience_text": None,
        "jd_text": "",
    }
    return base | kw


def test_hard_filters() -> None:
    """Only the skip list and avoided titles drop jobs; location only orders."""
    assert hard_filters.check(job(), Preferences()) is None
    assert "skip list" in hard_filters.check(job(), Preferences(blacklist=["Acme Pvt Ltd"]))  # type: ignore[operator]
    assert "avoid" in hard_filters.check(
        job(title_norm="senior data analyst"), Preferences(titles_avoid=["Senior"])
    )  # type: ignore[operator]
    # Location and experience never drop a job (decision 27): location only orders them.
    assert hard_filters.check(job(city="pune"), Preferences(locations=["Bangalore"])) is None
    assert hard_filters.check(job(experience_text="8-12 Yrs"), Preferences(exp_max_years=3)) is None
    prefs = Preferences(locations=["Bangalore"])
    assert location_rank(job(city="bengaluru"), prefs) == YOUR_CITY
    assert location_rank(job(city="pune", remote=True), prefs) == REMOTE
    assert location_rank(job(city="pune"), prefs) == ELSEWHERE
    assert location_rank(job(city=None, remote=True), Preferences(remote_ok=False)) == ELSEWHERE


def test_queue_age_decay() -> None:
    """Older queued jobs score lower."""
    now = datetime(2026, 10, 3, tzinfo=UTC)
    assert effective_score(80, now, now) == 80
    assert effective_score(80, now - timedelta(days=5), now) < effective_score(
        80, now - timedelta(days=1), now
    )


def test_posting_from_share() -> None:
    """A shared message yields the link, company and location."""
    p = posting_from_share(
        "Data Engineer at Orbit Retail - Pune\nCompany: Orbit Retail\nLocation: Pune\n"
        "https://www.linkedin.com/jobs/view/4000000001/ \nWe need Spark and SQL.",
        9,
    )
    assert p is not None and p.url == "https://www.linkedin.com/jobs/view/4000000001/"
    assert (p.company, p.location, p.source.value) == ("Orbit Retail", "Pune", "shared")
    assert posting_from_share("no link here", 1) is None
