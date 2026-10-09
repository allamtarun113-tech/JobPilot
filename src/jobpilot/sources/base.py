"""The JobSource interface: fetch(cursor) -> postings and a new cursor. Every source implements it.

A fetch does network work only - no database transaction. The caller saves the postings and the new cursor
together in one short transaction, and only when the fetch succeeded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from jobpilot.models.job import JobPosting, Lead
from jobpilot.models.preferences import Preferences


@dataclass
class FetchResult:
    """What one source fetch returns: postings, the new cursor, stats and notes."""

    postings: list[JobPosting]
    cursor: dict[str, Any]
    stats: dict[str, int] = field(default_factory=dict)  # e.g. emails read, jobs parsed, API calls
    notes: list[str] = field(default_factory=list)


class SourceUnavailable(Exception):
    """The source is not configured (no token / key yet) - skipped, reported in the summary."""


class JobSource(Protocol):
    """The one interface every job source implements."""

    name: str  # cursor key, e.g. "jobs_api"
    kind: str  # domain.job.SourceKind value

    async def fetch(self, cursor: dict[str, Any] | None) -> FetchResult:
        """Fetch postings since ``cursor`` (None on the first run).

        Network work only; the caller saves the postings and the
        returned
        cursor together, and only if this call succeeds.
        """


class LeadFinder(Protocol):
    """What the Job Finder source needs from the Job Finder agent (wired by the worker; discovery never
    imports the agent)."""

    async def find_leads(
        self, prefs: Preferences, seen_urls: set[str], *, run_id: int | None, offset: int = 0
    ) -> list[Lead]:
        """Return new job leads from web search for the user's titles.

        Args:
            prefs: The user's preferences (titles, places).
            seen_urls: Lead URLs already seen; never returned again.
            run_id: The run whose search budget is used.
            offset: Where to continue in the rotating query pool.
        """

    def searches_and_pages(self, run_id: int | None) -> tuple[int, int]:
        """Return (searches, pages) used so far in this run."""
