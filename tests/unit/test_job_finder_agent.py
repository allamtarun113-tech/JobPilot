"""The Job Finder only trusts observed URLs and accepts a full JD after strict identity matching."""

from typing import Any

from jobpilot.models.preferences import Preferences
from jobpilot.sources.finder_agent import (
    LEAD_SEARCHES_MAX,
    LEAD_SEARCHES_MIN,
    Decision,
    JobFinderAgent,
    lead_queries,
)
from jobpilot.sources.jd_enrich import find_full_jd


class FakeRuntime:
    """A fake agent runtime with scripted decisions, one search result and a page."""

    def __init__(self, decisions: list[Decision], page: dict[str, Any] | None = None) -> None:
        """Use these decisions in order and this page for fetches."""
        self.decisions = decisions
        self.page = page or {}
        self.cfg = {"max_pages_per_lead": 3}
        self.last_key: object = None
        self.forgotten: object = None
        self.searches: list[str] = []
        self.fetches: list[str] = []

    async def decide(self, prompt: Any, output: Any, *, run_id: int | None, purpose: str) -> Decision:
        """Return the next scripted decision."""
        return self.decisions.pop(0)

    async def search(self, query: str, *, run_id: int | None, **_kwargs: object) -> list[dict[str, str]]:
        """Record the query and return one Acme result."""
        self.searches.append(query)
        return [
            {
                "title": "Data Analyst at Acme Analytics",
                "url": "https://careers.acme.example/data-analyst",
                "snippet": "Acme Analytics is hiring in Bengaluru",
            }
        ]

    async def fetch(self, url: str, *, run_id: int | None) -> dict[str, Any]:
        """Record the URL and return the fixed page."""
        self.fetches.append(url)
        return self.page


async def test_leads_must_come_from_search_results() -> None:
    """A lead whose URL search never returned is dropped."""
    runtime = FakeRuntime(
        [
            *[
                Decision(action="search", query=f"data analyst Bengaluru {i}")
                for i in range(LEAD_SEARCHES_MIN)
            ],
            Decision(
                action="finish",
                leads=[
                    {
                        "title": "Data Analyst",
                        "company": "Acme Analytics",
                        "url": "https://careers.acme.example/data-analyst",
                        "snippet": "SQL",
                    },
                    {
                        "title": "Invented",
                        "company": "Fake",
                        "url": "https://invented.example/job",
                    },
                ],
            ),
        ]
    )
    agent = JobFinderAgent(runtime)  # type: ignore[arg-type]
    leads = await agent.find_leads(
        Preferences(titles_target=["Data Analyst"], locations=["Bengaluru"]), set(), run_id=1
    )
    assert [lead.company for lead in leads] == ["Acme Analytics"]


async def test_lead_search_is_wide_but_bounded() -> None:
    """Code runs the lead searches; the model can't add more."""
    lead = {
        "title": "Data Analyst",
        "company": "Acme Analytics",
        "url": "https://careers.acme.example/data-analyst",
    }
    runtime = FakeRuntime(
        [
            Decision(action="search", query="extra"),  # code already ran the maximum: refused
            Decision(action="finish", leads=[lead]),
        ]
    )
    prefs = Preferences(titles_target=["Data Analyst"])
    leads = await JobFinderAgent(runtime).find_leads(prefs, set(), run_id=1)  # type: ignore[arg-type]
    assert runtime.searches == lead_queries(prefs)  # searched by code, not the model
    assert len(runtime.searches) == LEAD_SEARCHES_MAX and [x.company for x in leads] == ["Acme Analytics"]


def test_lead_searches_rotate_through_titles_places_levels_and_sites() -> None:
    """The query pool covers every combination and rotates between runs."""
    from jobpilot.sources.finder_agent import lead_query_pool

    prefs = Preferences(titles_target=["AI Engineer"], locations=["Hyderabad", "Bengaluru"], remote_ok=True)
    pool = lead_query_pool(prefs)
    assert len(pool) == 3 * 4 * 1 * 4  # levels x (2 cities, remote India, India) x titles x searches
    assert "linkedin junior AI Engineer job Bengaluru posted this week" in pool
    assert "site:jobs.lever.co AI Engineer Hyderabad" in pool
    first, second = lead_queries(prefs, 0), lead_queries(prefs, LEAD_SEARCHES_MAX)
    assert not set(first) & set(second)  # the next run continues with new searches
    assert lead_queries(prefs, len(pool)) == first  # and wraps round


def decisions(company: str = "Acme Analytics", title: str = "Data Analyst") -> list[Decision]:
    """Return a search, fetch, finish script that picks the given company and title."""
    url = "https://careers.acme.example/data-analyst"
    return [
        Decision(action="search", query="Acme Analytics Data Analyst careers"),
        Decision(action="fetch", url=url),
        Decision(action="finish", selected_url=url, selected_company=company, selected_title=title),
    ]


async def test_full_jd_requires_company_and_title_match() -> None:
    """A JD is accepted only when company and title both match."""
    page = {
        "title": "Data Analyst - Acme Analytics Careers",
        "text": "Acme Analytics Data Analyst. " + "SQL Python dashboards and retail analytics. " * 20,
    }
    job = {"title": "Data Analyst", "company": "Acme Analytics", "jd_text": "snippet"}
    good = JobFinderAgent(FakeRuntime(decisions(), page))  # type: ignore[arg-type]
    found = await good.find(job, 1)
    assert found is not None and found[1].startswith("job_finder:")

    wrong_company = JobFinderAgent(FakeRuntime(decisions(company="Other Corp"), page))  # type: ignore[arg-type]
    assert await wrong_company.find(job, 2) is None
    wrong_title = JobFinderAgent(FakeRuntime(decisions(title="Office Manager"), page))  # type: ignore[arg-type]
    assert await wrong_title.find(job, 3) is None


async def test_agent_cannot_fetch_unobserved_url() -> None:
    """A URL that wasn't in search results is never fetched."""
    runtime = FakeRuntime(
        [
            Decision(action="fetch", url="https://unobserved.example/job"),
            Decision(action="finish"),
        ]
    )
    agent = JobFinderAgent(runtime)  # type: ignore[arg-type]
    assert await agent.find({"title": "X", "company": "Y", "jd_text": ""}, 1) is None
    assert runtime.fetches == []


async def test_agent_jd_lookup_runs_only_after_cheap_lookup_fails() -> None:
    """The agent is used only when the cheap lookup finds nothing."""

    class Cheap:
        """A cheap lookup with a fixed answer."""

        def __init__(self, answer: tuple[str, str] | None) -> None:
            """Always answer ``answer``."""
            self.answer = answer

        async def find(self, job: dict[str, Any]) -> tuple[str, str] | None:
            """Return the fixed answer."""
            return self.answer

    class Agent:
        """An agent lookup that counts its calls."""

        def __init__(self) -> None:
            """Start with no calls."""
            self.calls = 0

        async def find(self, job: dict[str, Any], run_id: int | None = None) -> tuple[str, str] | None:
            """Count the call and return a full JD."""
            self.calls += 1
            return ("agent full JD " * 30, "job_finder:https://careers.example/job")

    agent = Agent()
    cheap_text = "cheap full JD " * 30
    assert await find_full_jd({}, Cheap((cheap_text, "greenhouse")), agent, 5) == (
        cheap_text,
        "greenhouse",
    )
    assert agent.calls == 0
    found = await find_full_jd({}, Cheap(None), agent, 5)
    assert found is not None and found[1].startswith("job_finder:") and agent.calls == 1


def test_linkedin_leads_are_read_from_result_titles_never_fetched() -> None:
    """LinkedIn leads come from result titles; listing pages are ignored."""
    from jobpilot.sources.finder_agent import linkedin_lead

    at = linkedin_lead(
        {
            "title": "AI Engineer at CGI — Hyderabad, Telangana, India | LinkedIn",
            "url": "https://in.linkedin.com/jobs/view/ai-engineer-at-cgi-4465316817",
            "snippet": "Build GenAI apps",
        }
    )
    assert at is not None and (at.company, at.title, at.location) == (
        "CGI",
        "AI Engineer",
        "Hyderabad, Telangana, India",
    )
    hiring = linkedin_lead(
        {
            "title": "SensoVision Systems hiring Junior AIML Engineer in Bengaluru",
            "url": "https://in.linkedin.com/jobs/view/junior-aiml-engineer-at-sensovision-systems-4440620",
        }
    )
    assert hiring is not None and (hiring.company, hiring.title) == (
        "SensoVision Systems",
        "Junior AIML Engineer",
    )
    listing = {
        "title": "Machine Learning Engineer Jobs in Bengaluru (9,000+)",
        "url": "https://in.linkedin.com/jobs/ml-jobs",
    }
    assert linkedin_lead(listing) is None


async def test_hiring_email_task_returns_only_addresses_it_actually_saw() -> None:
    """Only addresses seen in results are returned, on their own budget."""
    from jobpilot.sources.finder_agent import Contact, find_contacts

    class ContactRuntime(FakeRuntime):
        """A runtime whose search result shows one careers address."""

        async def search(self, query: str, *, run_id: object = None, **_kw: object) -> list[dict[str, str]]:
            """Record the usage key and return the result with the address."""
            self.searches.append(query)
            self.last_key = run_id
            return [
                {
                    "title": "Acme careers",
                    "url": "https://acme.example/careers",
                    "snippet": "Write to careers@acme.example",
                }
            ]

        def forget(self, key: object) -> None:
            """Record which usage key was freed."""
            self.forgotten = key

    runtime = ContactRuntime(
        [
            Decision(
                action="finish",
                contacts=[Contact(email="careers@acme.example"), Contact(email="guessed@acme.example")],
            )
        ]
    )
    found = await find_contacts(JobFinderAgent(runtime), "Acme", 7)  # type: ignore[arg-type]
    assert found == [{"email": "careers@acme.example", "source": "web search (https://acme.example/careers)"}]
    assert (
        runtime.last_key == "contacts:7" and runtime.forgotten == "contacts:7"
    )  # its own budget, then freed
