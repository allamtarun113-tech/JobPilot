"""Bounded Job Finder agent. It finds leads and full JDs; it never filters or scores."""

from __future__ import annotations

import json
import re
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator

from jobpilot.llm import prompts
from jobpilot.llm.agent_runtime import AgentLimit, AgentRuntime, StepLimit, preferred_domains
from jobpilot.models.job import Lead
from jobpilot.models.preferences import Preferences
from jobpilot.reference import normalize
from jobpilot.safety import sanitize
from jobpilot.sources.enrich_lookup import title_matches

# Job boards and company-review sites list jobs but are not employers; their search pages are not jobs.
AGGREGATORS = {
    "ambitionbox",
    "glassdoor",
    "naukri",
    "indeed",
    "linkedin",
    "instahyre",
    "foundit",
    "monster",
    "shine",
    "internshala",
    "jobleads",
    "timesjobs",
    "apna",
    "hirist",
    "iimjobs",
    "freshersworld",
    "simplyhired",
    "ziprecruiter",
    "jooble",
    "careerjet",
    "levelsfyi",
    "cutshort",
}
_LISTING_TITLE = re.compile(
    r"\b\d[\d,]*\+?\s+.*\bjobs?\b|\bjobs?\s+(in|at|for|near)\b"
    r"|\b(job openings|vacancies|openings in|hiring now)\b",
    re.I,
)


def is_listing(lead: Lead) -> bool:
    """A search-results or listing page ("500+ AI Engineer Jobs in Chennai", company "AmbitionBox")."""
    # Drop a domain-like tail, so "naukri.com" -> "naukri".
    company = re.sub(r"(com|in|co)$", "", normalize.company_norm(lead.company).replace(" ", ""))
    return bool(_LISTING_TITLE.search(lead.title)) or company in AGGREGATORS


# One find_leads batch: one search is too narrow (it found nothing once its results were all listing pages),
# and more than 8 don't fit the model's view (the prompt shows the last 8 observations). A run searches
# `agents.job_finder.lead_searches_per_run` times, in batches of LEAD_SEARCHES_MAX.
LEAD_SEARCHES_MIN = 4
LEAD_SEARCHES_MAX = 8
# Searches that return ONE posting per result. Plain "<title> <city> jobs" searches return job-board listing
# pages, which are never leads. LinkedIn job pages are only seen as search results - never fetched; for
# LinkedIn a plain-language query works and `site:` returns nothing. Naukri via search returned stale,
# unrelated postings (2026-10-06 test), so Naukri comes through the jobs API instead.
LEAD_TEMPLATES = ("linkedin {level}{title} job {place} posted this week",
                  "site:jobs.lever.co {level}{title} {place}", "site:jobs.ashbyhq.com {level}{title} {place}",
                  "site:boards.greenhouse.io {level}{title} {place}")  # fmt: skip
LEAD_LEVELS = ("", "junior ", "entry level ")


def lead_query_pool(prefs: Preferences) -> list[str]:
    """Every lead search for your titles (targets + related) x sites x places x levels. Titles vary fastest,
    then sites, so each run's slice covers many titles on every site; runs rotate through the pool, so each
    run sees fresh results instead of repeating the same searches."""
    places = list(dict.fromkeys([*prefs.locations, *(["remote India"] if prefs.remote_ok else []), "India"]))
    return [
        template.format(level=level, title=title, place=place)
        for level in LEAD_LEVELS
        for place in places
        for template in LEAD_TEMPLATES
        for title in prefs.search_titles()
    ]


def lead_queries(prefs: Preferences, offset: int = 0, budget: int = LEAD_SEARCHES_MAX) -> list[str]:
    """The next `budget` searches of the pool, starting at `offset` (wrapping round)."""
    pool = lead_query_pool(prefs)
    if not pool:
        return []
    return [pool[(offset + i) % len(pool)] for i in range(min(budget, len(pool)))]


class Contact(BaseModel):
    """A published contact address the agent saw, with its page."""

    email: str = Field(max_length=200)
    url: str = Field(default="", max_length=2000)


class Decision(BaseModel):
    """The agent's next move: search, fetch, or finish with its findings."""

    action: Literal["search", "fetch", "finish"]
    query: str | None = Field(default=None, max_length=600)
    url: str | None = Field(default=None, max_length=4000)
    leads: list[Lead] = Field(default_factory=list, max_length=60)
    selected_url: str | None = Field(default=None, max_length=4000)
    selected_company: str | None = Field(default=None, max_length=200)
    selected_title: str | None = Field(default=None, max_length=200)
    contacts: list[Contact] = Field(default_factory=list, max_length=20)
    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def required_for_action(self) -> Decision:
        """Require a query for search and a URL for fetch.

        Returns:
            The decision itself.

        Raises:
            ValueError: The needed field is missing (the model retries).
        """
        if self.action == "search" and not self.query:
            raise ValueError("search needs query")
        if self.action == "fetch" and not self.url:
            raise ValueError("fetch needs url")
        return self


_LINKEDIN_AT = re.compile(
    r"^(?P<title>.+?) at (?P<company>.+?) [\u2014\u2013-] (?P<place>.+?)(?: \| LinkedIn)?$"
)
_LINKEDIN_HIRING = re.compile(r"^(?P<company>.+?) hiring (?P<title>.+?) in (?P<place>.+?)(?: \| LinkedIn)?$")


def linkedin_lead(result: dict[str, str]) -> Lead | None:
    """A LinkedIn job page seen in search results, read from its result title in code (the page itself is
    never fetched): 'AI Engineer at CGI - Hyderabad, Telangana, India | LinkedIn' or 'SensoVision Systems
    hiring Junior AIML Engineer in Bengaluru'. Anything else (a 'jobs in Hyderabad' listing) is no lead."""
    url = str(result.get("url") or "")
    if "linkedin.com/jobs/view/" not in url:
        return None
    title = " ".join(str(result.get("title") or "").split())
    m = _LINKEDIN_AT.match(title) or _LINKEDIN_HIRING.match(title)
    if not m:
        return None
    return Lead(
        title=m["title"][:200],
        company=m["company"][:200],
        url=url,
        snippet=str(result.get("snippet") or "")[:4000],
        location=m["place"][:200],
    )


def _brief(results: list[dict[str, str]]) -> list[dict[str, str]]:
    """Search results as the model sees them. Long snippets once filled the 30k-character observation window,
    so the later searches (LinkedIn among them) were cut off before the model saw them."""
    return [{**r, "snippet": str(r.get("snippet") or "")[:240]} for r in results]


def _prompt(task: str, context: dict[str, Any], observations: list[dict[str, Any]]):
    """Render the agent prompt with context and recent observations fenced as data.

    Only the last 8 observations are shown, capped at 30,000
    characters.
    """
    return prompts.render(
        "agents/job_finder",
        task=task,
        context=sanitize.fence("context", json.dumps(context, ensure_ascii=True)),
        observations=sanitize.fence(
            "observations", json.dumps(observations[-8:], ensure_ascii=True)[:30_000]
        ),
    )


class JobFinderAgent:
    """The bounded Job Finder agent: finds leads and full JDs, never filters."""

    def __init__(self, runtime: AgentRuntime) -> None:
        """Create the agent on a bounded runtime (caps per run and per task)."""
        self.runtime = runtime
        self.searched: list[dict[str, str]] = []  # results of the searches code ran in the last task
        self.result_urls: set[str] = set()  # every URL the searches of the last task returned
        self.jd_lookups: dict[int | None, int] = {}  # agent JD lookups per run

    async def _run(
        self,
        task: str,
        context: dict[str, Any],
        *,
        run_id: int | None,
        queries: list[str] | None = None,
        batch: int | None = None,
        usage_key: str | None = None,
    ) -> tuple[Decision, dict[str, dict[str, Any]], set[str]]:
        """`queries` are searched by code first; their results start the observations. `usage_key`: count
        this task's searches, pages and tokens apart from any run (a hiring-email lookup)."""
        key: int | str | None = usage_key or run_id
        observations: list[dict[str, Any]] = []
        fetched: dict[str, dict[str, Any]] = {}
        result_urls: set[str] = set()
        self.result_urls = result_urls
        pages_for_task = 0
        searches = 0
        self.searched = []
        for query in queries or []:
            results = await self.runtime.search(query, run_id=key)
            self.searched += results
            result_urls.update(str(item["url"]) for item in results if item.get("url"))
            observations.append({"tool": "search", "query": query, "results": _brief(results)})
            searches += 1
        # Steps are capped per task; each batch counts as its own task.
        purpose = f"{task}:{context.get('job_id', 'run')}" + (f":{batch}" if batch is not None else "")
        while True:
            decision = await self.runtime.decide(
                _prompt(task, context, observations), Decision, run_id=key, purpose=purpose
            )
            leads_task = task == "find_leads"
            if decision.action == "finish":
                # The model may not quit before searching a few times.
                if leads_task and searches < LEAD_SEARCHES_MIN:
                    observations.append(
                        {
                            "tool": "finish",
                            "error": f"only {searches} search(es) so far: search at least {LEAD_SEARCHES_MIN}"
                            " times (each target title and location, and the employer job systems) first",
                        }
                    )
                    continue
                return decision, fetched, result_urls
            if decision.action == "search" and leads_task and searches >= LEAD_SEARCHES_MAX:
                observations.append(
                    {"tool": "search", "error": "search limit for this task reached: finish with the leads"}
                )
                continue
            if decision.action == "search":
                searches += 1
                results = await self.runtime.search(
                    decision.query or "", run_id=key, company=str(context.get("company") or "") or None
                )
                result_urls.update(str(item["url"]) for item in results if item.get("url"))
                observations.append({"tool": "search", "query": decision.query, "results": _brief(results)})
                continue
            url = decision.url or ""
            if leads_task:  # leads come from search results; a page fetch only spends steps
                observations.append({"tool": "fetch", "url": url, "error": "find_leads uses search only"})
                continue
            # Only pages that came from search results may be fetched.
            if url not in result_urls:
                observations.append({"tool": "fetch", "url": url, "error": "URL was not in search results"})
                continue
            if pages_for_task >= self.runtime.cfg["max_pages_per_lead"]:
                observations.append({"tool": "fetch", "url": url, "error": "per-lead page cap reached"})
                continue
            try:
                page = await self.runtime.fetch(url, run_id=key)
            except RuntimeError as exc:
                observations.append({"tool": "fetch", "url": url, "error": str(exc)})
            else:
                pages_for_task += 1
                fetched[url] = page
                observations.append(
                    {
                        "tool": "fetch",
                        "url": url,
                        "title": page.get("title"),
                        "text": str(page.get("text") or "")[:12_000],
                    }
                )

    def searches_and_pages(self, run_id: int | None) -> tuple[int, int]:
        """Return (searches, pages) this run has used so far."""
        use = self.runtime.usage(run_id)
        return use.searches, use.pages

    async def find_leads(
        self, prefs: Preferences, seen_urls: set[str], *, run_id: int | None, offset: int = 0
    ) -> list[Lead]:
        """Leads from this run's search budget, in batches. A batch that hits its step cap still gives its
        LinkedIn leads (read from the results by code); a run cap ends the search, keeping what was found."""
        titles = prefs.search_titles()
        if not titles:
            return []
        # Already-seen URLs are filtered in code below; sending them to the model only spends tokens.
        context = {
            "titles_target": titles,
            "locations": prefs.locations,
            "remote_ok": prefs.remote_ok,
            "preferred_careers_sites": list(preferred_domains()),
        }
        budget = int(self.runtime.cfg.get("lead_searches_per_run", LEAD_SEARCHES_MAX))
        queries = lead_queries(prefs, offset, budget)
        accepted: list[Lead] = []
        batches = range(0, len(queries), LEAD_SEARCHES_MAX)
        failures: list[RuntimeError] = []
        for number, start in enumerate(batches):
            batch = queries[start : start + LEAD_SEARCHES_MAX]
            stop = False
            try:
                decision, _fetched, _urls = await self._run(
                    "find_leads", context, run_id=run_id, queries=batch, batch=number
                )
                found = list(decision.leads)
            except StepLimit:
                found = []
            except AgentLimit:
                found, stop = [], True
            except RuntimeError as exc:  # a web search failed: keep this batch's results so far, go on
                failures.append(exc)
                found = []
            # LinkedIn result titles are parsed by code, whatever the
            # model did.
            found += [lead for r in self.searched if (lead := linkedin_lead(r)) is not None]
            for lead in found:
                # Keep only http(s) leads that search really returned,
                # that are new,
                # and that aren't listing pages.
                if (
                    urlsplit(lead.url).scheme in ("http", "https")
                    and lead.url in self.result_urls
                    and lead.url not in seen_urls
                    and lead.url not in {a.url for a in accepted}
                    and not is_listing(lead)
                ):
                    accepted.append(lead)
            if stop:
                break
        if failures and len(failures) == len(batches):
            raise failures[-1]  # every batch failed: the source fails visibly and its cursor stays
        return accepted

    async def find(self, job: dict[str, Any], run_id: int | None = None) -> tuple[str, str] | None:
        """Find a lead's full JD with the agent (the last-resort lookup).

        The page the agent picks is accepted only if its company and
        title
        match the lead and the page text names both.

        Args:
            job: The job row (title, company, apply_url, jd_text, ...).
            run_id: The run whose caps apply.

        Returns:
            (JD text, where it came from), or None.
        """
        # Agent JD lookups are capped per run (they cost the most
        # tokens).
        cap = self.runtime.cfg.get("max_jd_lookups_per_run")
        if cap is not None and self.jd_lookups.get(run_id, 0) >= int(cap):
            return None
        self.jd_lookups[run_id] = self.jd_lookups.get(run_id, 0) + 1
        context = {
            "job_id": job.get("id"),
            "title": job["title"],
            "company": job["company"],
            "location": job.get("location_raw") or job.get("city"),
            "lead_url": job.get("apply_url"),
            "snippet": str(job.get("jd_text") or "")[:4000],
        }
        try:
            decision, fetched, _result_urls = await self._run("find_full_jd", context, run_id=run_id)
        except (AgentLimit, RuntimeError):
            return None
        url = decision.selected_url or ""
        page = fetched.get(url)
        if page is None or not decision.selected_company or not decision.selected_title:
            return None
        # Code checks the agent's pick; the agent never decides alone.
        if normalize.company_norm(decision.selected_company) != normalize.company_norm(job["company"]):
            return None
        if not title_matches(decision.selected_title, job["title"]):
            return None
        text = str(page.get("text") or "").strip()
        page_title = str(page.get("title") or "")
        company = normalize.company_norm(job["company"])
        haystack = normalize.company_norm(f"{page_title} {text[:5000]}")
        page_title_ok = title_matches(page_title, job["title"])
        title_in_text = normalize.title_norm(job["title"]) in normalize.title_norm(text[:1500])
        if company not in haystack or not (page_title_ok or title_in_text) or len(text) < 200:
            return None
        return text, f"job_finder:{url}"


CONTACT_SEARCHES = ("{company} careers email", "{company} HR email address", "{company} contact email jobs")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")


async def find_contacts(agent: JobFinderAgent, company: str, job_id: int) -> list[dict[str, str]]:
    """The hiring-email task: published addresses the agent saw for `company`, each with where it was seen.
    Only addresses that literally appear in an observed search result or fetched page are returned; code in
    hr_email decides which belong to the company and may be used. Its own usage budget per job."""
    key = f"contacts:{job_id}"
    try:
        decision, fetched, _urls = await agent._run(
            "find_hr_email",
            {"job_id": key, "company": company},
            run_id=None,
            queries=[q.format(company=company) for q in CONTACT_SEARCHES],
            usage_key=key,
        )
    except (AgentLimit, RuntimeError):
        return []
    finally:
        agent.runtime.forget(key)
    seen: dict[str, str] = {}
    for result in agent.searched:
        text = f"{result.get('title') or ''} {result.get('snippet') or ''}"
        for email in _EMAIL.findall(text):
            seen.setdefault(email.lower(), f"web search ({result.get('url')})")
    for url, page in fetched.items():
        for email in _EMAIL.findall(str(page.get("text") or "")):
            seen.setdefault(email.lower(), url)
    out = [{"email": e, "source": src} for e, src in seen.items()]
    claimed = {c.email.lower() for c in decision.contacts}
    return sorted(out, key=lambda c: c["email"] not in claimed)  # the agent's picks first
