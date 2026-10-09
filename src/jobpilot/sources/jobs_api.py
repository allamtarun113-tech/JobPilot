"""Jobs API source (JSearch / Google for Jobs). Queries come from the preferences: titles x locations.

Cursor: last seen posting time + the ids already seen; the date bucket is the smallest that covers the gap.
First run: last 3 days.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from jobpilot.core import clock
from jobpilot.integrations.jobs_api import JobsApi, QuotaExceeded
from jobpilot.models.job import JobPosting, SourceKind
from jobpilot.models.preferences import Preferences
from jobpilot.sources.base import FetchResult, SourceUnavailable

MAX_QUERIES = 6
SEEN_IDS_KEPT = 1000


def date_bucket(last_seen: datetime | None) -> str:
    """Return the smallest posting-date bucket that covers the gap since ``last_seen``."""
    if last_seen is None:
        return "3days"
    gap = (clock.now() - last_seen).total_seconds() / 86400
    if gap <= 1:
        return "today"
    if gap <= 3:
        return "3days"
    if gap <= 7:
        return "week"
    return "month"


def queries(prefs: Preferences) -> list[str]:
    """Return the search queries (titles x places), at most MAX_QUERIES."""
    places = prefs.locations or ["India"]
    if prefs.remote_ok and "remote" not in [p.lower() for p in places]:
        places = [*places, "remote"]
    return [f"{title} in {place}" for title in prefs.titles_target for place in places][:MAX_QUERIES]


class JobsApiSource:
    """Jobs from the jobs API (JSearch, SerpApi fallback) as a JobSource."""

    name = "jobs_api"
    kind = SourceKind.API.value

    def __init__(self, client: JobsApi | None, prefs: Preferences) -> None:
        """Create the source with a jobs API client (None when unconfigured)."""
        self.client = client
        self.prefs = prefs

    async def fetch(self, cursor: dict[str, Any] | None) -> FetchResult:
        """Run every query for the posting dates since the cursor.

        Raises:
            SourceUnavailable: No API key, no target titles, or the
                daily
                quota ran out (the cursor then stays where it was).
        """
        if self.client is None:
            raise SourceUnavailable("no JSEARCH_API_KEY or SERPAPI_API_KEY")
        qs = queries(self.prefs)
        if not qs:
            raise SourceUnavailable("set 'Titles to target' in Preferences to search the jobs API")
        last_seen = (
            datetime.fromisoformat(cursor["last_posted_at"])
            if cursor and cursor.get("last_posted_at")
            else None
        )
        seen: list[str] = list(cursor.get("seen_ids", [])) if cursor else []
        seen_set = set(seen)
        bucket = date_bucket(last_seen)
        calls_before = self.client.calls
        postings: list[JobPosting] = []
        newest = last_seen
        for q in qs:
            try:
                items = await self.client.search(q, date_posted=bucket)
            except QuotaExceeded as exc:  # the cursor must not move past queries that never ran
                raise SourceUnavailable(str(exc)) from exc
            for item in items:
                job_id = str(item.get("job_id"))
                # The same job often comes back for several queries.
                if not job_id or job_id in seen_set:
                    continue
                seen_set.add(job_id)
                seen.append(job_id)
                posting = self._posting(item)
                if posting.posted_at and (newest is None or posting.posted_at > newest):
                    newest = posting.posted_at
                postings.append(posting)
        cursor_out = {
            "last_posted_at": (newest or clock.now()).isoformat(),
            "seen_ids": seen[-SEEN_IDS_KEPT:],
        }
        stats = {"fetched": len(postings), "api_calls": self.client.calls - calls_before}
        return FetchResult(postings, cursor_out, stats, [f"date bucket: {bucket}"])

    def _posting(self, item: dict[str, Any]) -> JobPosting:
        """Turn one JSearch row into a JobPosting."""
        ts = item.get("job_posted_at_timestamp")
        posted = datetime.fromtimestamp(ts, UTC) if isinstance(ts, (int, float)) else None
        location = ", ".join(
            x for x in (item.get("job_city"), item.get("job_state"), item.get("job_country")) if x
        )
        return JobPosting(
            source=SourceKind.API,
            external_id=f"jsearch:{item['job_id']}",
            title=item.get("job_title") or "",
            company=item.get("employer_name") or "",
            location=location or None,
            url=item.get("job_apply_link") or item.get("job_google_link") or "",
            description=item.get("job_description") or "",
            description_is_snippet=not bool(item.get("job_description")),
            posted_at=posted,
            remote=bool(item.get("job_is_remote")),
            raw={"jsearch_id": item["job_id"], "publisher": item.get("job_publisher")},
        )
