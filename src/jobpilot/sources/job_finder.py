"""Job Finder search-result leads as a normal cursor-based JobSource."""

from __future__ import annotations

import hashlib
from typing import Any

from jobpilot.models.job import JobPosting, SourceKind
from jobpilot.models.preferences import Preferences
from jobpilot.sources.base import FetchResult, LeadFinder, SourceUnavailable

SEEN_URLS_KEPT = 2000


class JobFinderSource:
    """The Job Finder agent's web-search leads, as a cursor-based JobSource."""

    name = "job_finder"
    kind = SourceKind.SEARCH.value

    def __init__(self, agent: LeadFinder | None, prefs: Preferences, run_id: int | None) -> None:
        """Create the source.

        Args:
            agent: The lead finder, or None when no OpenAI key is set.
            prefs: The user's preferences.
            run_id: The run whose search budget is used.
        """
        self.agent = agent
        self.prefs = prefs
        self.run_id = run_id

    async def fetch(self, cursor: dict[str, Any] | None) -> FetchResult:
        """Find new leads and turn them into snippet postings.

        The cursor keeps the last 2,000 lead URLs seen and where the
        query
        rotation stopped.

        Raises:
            SourceUnavailable: No OpenAI key is set.
        """
        if self.agent is None:
            raise SourceUnavailable("no OPENAI_API_KEY")
        seen = list(cursor.get("seen_urls", [])) if cursor else []
        offset = int(cursor.get("query_offset", 0)) if cursor else 0
        searches, pages = self.agent.searches_and_pages(self.run_id)
        leads = await self.agent.find_leads(self.prefs, set(seen), run_id=self.run_id, offset=offset)
        postings = [
            JobPosting(
                source=SourceKind.SEARCH,
                # A stable id from the URL, so a repeat lead is the same
                # sighting.
                external_id=f"search:{hashlib.sha256(lead.url.encode()).hexdigest()[:24]}",
                title=lead.title,
                company=lead.company,
                location=lead.location,
                url=lead.url,
                description=lead.snippet,
                description_is_snippet=True,
                raw={"search_lead": True},
            )
            for lead in leads
        ]
        seen.extend(lead.url for lead in leads)
        searches_after, pages_after = self.agent.searches_and_pages(self.run_id)
        return FetchResult(
            postings,
            {
                "seen_urls": list(dict.fromkeys(seen))[-SEEN_URLS_KEPT:],
                "query_offset": offset + (searches_after - searches),  # the next run continues the rotation
            },
            {
                "fetched": len(postings),
                "searches": searches_after - searches,
                "pages": pages_after - pages,
            },
        )
