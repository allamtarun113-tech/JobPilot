"""JSearch source and the cheap JD lookup (Greenhouse / Lever), with mocked HTTP."""

from datetime import UTC, datetime, timedelta

import httpx

from jobpilot.core import clock
from jobpilot.core.clock import FrozenClock
from jobpilot.integrations.jobs_api import JSearchClient
from jobpilot.models.preferences import Preferences
from jobpilot.sources.enrich_lookup import EnrichLookup
from jobpilot.sources.jobs_api import JobsApiSource, date_bucket, queries

JSEARCH_ITEM = {
    "job_id": "abc123==",
    "job_title": "Data Analyst",
    "employer_name": "Acme Analytics",
    "job_city": "Bengaluru",
    "job_state": "Karnataka",
    "job_country": "IN",
    "job_apply_link": "https://www.linkedin.com/jobs/view/3912345678/",
    "job_description": "Full JD " * 50,
    "job_posted_at_timestamp": 1790900000,
    "job_is_remote": False,
}


def jsearch_transport(seen: list[httpx.Request]) -> httpx.MockTransport:
    """Return a mock JSearch transport that records requests."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Answer the search endpoint with one job; anything else 404."""
        seen.append(request)
        if request.url.path == "/search-v2":
            return httpx.Response(200, json={"status": "OK", "data": [JSEARCH_ITEM]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_jobs_api_source_maps_results_and_cursor() -> None:
    """JSearch rows map to postings; the cursor skips seen ids."""
    seen: list[httpx.Request] = []
    client = JSearchClient("k", "jsearch.p.rapidapi.com", transport=jsearch_transport(seen))
    prefs = Preferences(titles_target=["Data Analyst"], locations=["Bengaluru"], remote_ok=False)
    result = await JobsApiSource(client, prefs).fetch(None)
    (p,) = result.postings
    assert (p.external_id, p.company, p.location) == (
        "jsearch:abc123==",
        "Acme Analytics",
        "Bengaluru, Karnataka, IN",
    )
    assert not p.description_is_snippet and result.stats["api_calls"] == 1
    assert seen[0].headers["x-rapidapi-key"] == "k" and seen[0].url.params["date_posted"] == "3days"
    again = await JobsApiSource(client, prefs).fetch(result.cursor)  # seen ids are skipped
    assert again.postings == []


def test_queries_and_buckets(frozen_clock: FrozenClock) -> None:
    """Queries combine titles and places; buckets cover the gap."""
    assert queries(Preferences()) == []
    assert queries(Preferences(titles_target=["Data Analyst"], locations=["Pune"])) == [
        "Data Analyst in Pune",
        "Data Analyst in remote",
    ]
    assert date_bucket(None) == "3days"
    assert date_bucket(clock.now() - timedelta(hours=10)) == "today"
    assert date_bucket(clock.now() - timedelta(days=5)) == "week"
    assert date_bucket(datetime(2020, 1, 1, tzinfo=UTC)) == "month"


async def test_greenhouse_lookup_requires_title_match() -> None:
    """The Greenhouse lookup returns only a title-matching job, as plain text."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Serve one Greenhouse board with two jobs."""
        if (
            request.url.host == "boards-api.greenhouse.io"
            and request.url.path == "/v1/boards/acmeanalytics/jobs"
        ):
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {"title": "Office Manager", "content": "&lt;p&gt;Not this one&lt;/p&gt;"},
                        {
                            "title": "Data Analyst - Retail",
                            "content": "&lt;p&gt;" + "Real JD text. " * 30 + "&lt;/p&gt;",
                        },
                    ]
                },
            )
        return httpx.Response(404)

    lookup = EnrichLookup(None, transport=httpx.MockTransport(handler))
    found = await lookup.find(
        {"id": 1, "company": "Acme Analytics", "company_norm": "acme analytics", "title": "Data Analyst"}
    )
    assert found is not None and found[1] == "greenhouse:acmeanalytics" and "Real JD text" in found[0]
    assert "<p>" not in found[0]
    assert await lookup.find({"id": 2, "company": "Nobody", "company_norm": "nobody", "title": "X"}) is None
