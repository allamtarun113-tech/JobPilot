"""Phase 2: wider search.

Related titles, the search budget in batches, free JD lookups from the
lead's link, and the agent JD lookup cap.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from jobpilot.models.preferences import Preferences, RelatedTitle
from jobpilot.sources import role_expander
from jobpilot.sources.enrich_lookup import EnrichLookup
from jobpilot.sources.finder_agent import LEAD_SEARCHES_MAX, Decision, JobFinderAgent, lead_queries
from tests.unit.test_job_finder_agent import FakeRuntime

NOW = datetime(2026, 10, 7, tzinfo=UTC)


def related(*titles: str) -> list[RelatedTitle]:
    """Return related titles with a fixed reason."""
    return [RelatedTitle(title=t, reason="same work") for t in titles]


def test_related_titles_refresh_weekly_or_when_targets_change() -> None:
    """Related titles refresh weekly or when targets change."""
    prefs = Preferences(titles_target=["AI Engineer"])
    assert role_expander.needs_refresh(prefs, NOW)  # never made
    fresh = prefs.model_copy(update={"related_titles_for": ["AI Engineer"], "related_titles_at": NOW})
    assert not role_expander.needs_refresh(fresh, NOW + timedelta(days=6))
    assert role_expander.needs_refresh(fresh, NOW + timedelta(days=7))
    changed = fresh.model_copy(update={"titles_target": ["AI Engineer", "Data Scientist"]})
    assert role_expander.needs_refresh(changed, NOW)
    assert not role_expander.needs_refresh(Preferences(), NOW)  # no targets: nothing to expand


def test_merge_keeps_your_list_and_never_brings_back_removed_titles() -> None:
    """Merging keeps your titles and never re-adds removed or avoided ones."""
    prefs = Preferences(
        titles_target=["AI Engineer"],
        titles_avoid=["Analyst"],
        related_titles=related("ML Engineer"),
        related_titles_removed=["Data Scientist"],
    )
    suggested = related("ML Engineer", "Data Scientist", "LLM Engineer", "AI Engineer", "Data Analyst")
    merged = role_expander.merge(prefs, suggested, NOW)
    assert [r.title for r in merged.related_titles] == ["ML Engineer", "LLM Engineer"]
    assert merged.related_titles_for == ["AI Engineer"] and merged.related_titles_at == NOW
    assert merged.search_titles() == ["AI Engineer", "ML Engineer", "LLM Engineer"]


def test_search_covers_many_titles_on_every_site() -> None:
    """One run's searches cover every title on every site."""
    titles = [f"Title {i}" for i in range(12)]
    prefs = Preferences(
        titles_target=titles[:2], related_titles=related(*titles[2:]), locations=["Bengaluru"]
    )
    first_run = lead_queries(prefs, 0, 48)
    assert len(first_run) == 48
    assert all(any(t in q for q in first_run) for t in titles)  # every title searched this run
    for site in ("linkedin", "jobs.lever.co", "jobs.ashbyhq.com", "boards.greenhouse.io"):
        assert any(site in q for q in first_run)


async def test_lead_search_runs_the_budget_in_batches_of_eight() -> None:
    """The search budget runs in batches; duplicate leads are kept once."""
    lead = {
        "title": "Data Analyst",
        "company": "Acme Analytics",
        "url": "https://careers.acme.example/data-analyst",
    }
    runtime = FakeRuntime([Decision(action="finish", leads=[lead]), Decision(action="finish", leads=[lead])])
    runtime.cfg["lead_searches_per_run"] = 2 * LEAD_SEARCHES_MAX
    prefs = Preferences(titles_target=["Data Analyst", "BI Analyst"], locations=["Pune"])
    leads = await JobFinderAgent(runtime).find_leads(prefs, set(), run_id=1)  # type: ignore[arg-type]
    assert runtime.searches == lead_queries(prefs, 0, 2 * LEAD_SEARCHES_MAX)
    assert [x.company for x in leads] == ["Acme Analytics"]  # the same lead twice is kept once


async def test_one_failed_batch_does_not_lose_the_others() -> None:
    """A failed batch doesn't lose the other batches' leads."""

    class Flaky(FakeRuntime):
        """A runtime whose first search fails."""

        async def search(self, query: str, **kw: Any) -> list[dict[str, str]]:
            """Fail the first search, then search normally."""
            if len(self.searches) == 0:
                self.searches.append(query)
                raise RuntimeError("web search failed")
            return await super().search(query, **kw)

    lead = {
        "title": "Data Analyst",
        "company": "Acme Analytics",
        "url": "https://careers.acme.example/data-analyst",
    }
    runtime = Flaky([Decision(action="finish", leads=[lead])])
    runtime.cfg["lead_searches_per_run"] = 2 * LEAD_SEARCHES_MAX
    prefs = Preferences(titles_target=["Data Analyst"], locations=["Pune", "Delhi"])
    leads = await JobFinderAgent(runtime).find_leads(prefs, set(), run_id=1)  # type: ignore[arg-type]
    assert [x.company for x in leads] == ["Acme Analytics"]


async def test_agent_jd_lookups_are_capped_per_run() -> None:
    """A second agent JD lookup in a run is refused before any step."""
    runtime = FakeRuntime([Decision(action="finish")])
    runtime.cfg["max_jd_lookups_per_run"] = 1
    agent = JobFinderAgent(runtime)  # type: ignore[arg-type]
    job = {"title": "X", "company": "Y", "jd_text": ""}
    assert await agent.find(job, 1) is None  # first lookup runs (and finds nothing)
    assert await agent.find(job, 1) is None and runtime.decisions == []  # second: refused before any step
    assert agent.jd_lookups == {1: 1}


JD = "Build retrieval and LLM features in Python. " * 10


def careers_api(request: httpx.Request) -> httpx.Response:
    """Answer like the Lever, Greenhouse and Ashby public APIs."""
    url = str(request.url)
    if url == "https://api.lever.co/v0/postings/acme/1b2c-3d":
        return httpx.Response(200, json={"text": "AI Engineer", "descriptionPlain": JD, "lists": []})
    if url == "https://boards-api.greenhouse.io/v1/boards/acme/jobs/4242":
        return httpx.Response(200, json={"title": "AI Engineer", "content": f"<p>{JD}</p>"})
    if url == "https://api.ashbyhq.com/posting-api/job-board/acme":
        return httpx.Response(
            200, json={"jobs": [{"id": "9f-1", "title": "AI Engineer", "descriptionPlain": JD}]}
        )
    return httpx.Response(404)


async def test_full_jd_from_the_leads_own_careers_link() -> None:
    """A lead's careers link yields its JD; wrong titles and odd paths don't."""
    lookup = EnrichLookup(None, transport=httpx.MockTransport(careers_api))
    base = {"id": 1, "title": "AI Engineer", "company": "Some Other Name", "company_norm": "some other name"}
    for url, where in (
        ("https://jobs.lever.co/acme/1b2c-3d", "lever:acme"),
        ("https://boards.greenhouse.io/acme/jobs/4242", "greenhouse:acme"),
        ("https://job-boards.greenhouse.io/acme/jobs/4242?gh_src=x", "greenhouse:acme"),
        ("https://jobs.ashbyhq.com/acme/9f-1", "ashby:acme"),
    ):
        found = await lookup.find({**base, "apply_url": url})
        assert found is not None and found[1] == where and "retrieval" in found[0]
    wrong_title = await lookup.find(
        {**base, "title": "Office Manager", "apply_url": "https://jobs.lever.co/acme/1b2c-3d"}
    )
    assert wrong_title is None
    odd = await lookup.find({**base, "apply_url": "https://jobs.lever.co/acme/..%2f..%2fx"})
    assert odd is None
