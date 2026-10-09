"""Jobs API clients (Google for Jobs): JSearch first, SerpApi as the fallback. Plain SDK-style calls, no MCP.

JSEARCH_HOST can point at another JSearch provider with the same API. Every paid call is written to the
api_usage ledger and refused once the daily quota (caps.jobs_api_calls_per_day) is used.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, Protocol

import httpx

from jobpilot.core import clock
from jobpilot.core.config import get_settings

log = logging.getLogger(__name__)
Recorder = Callable[[str, str], Awaitable[None]]  # (provider, endpoint) -> record it, or raise QuotaExceeded


class JobsApiError(RuntimeError):
    """A jobs API call failed (HTTP error, rate limit, no provider)."""

    pass


class QuotaExceeded(JobsApiError):
    """The daily jobs-API quota is used up; no call was made."""


class JobsApi(Protocol):
    """What the jobs source needs from a jobs API client."""

    @property
    def calls(self) -> int:
        """How many paid calls this client has made."""

    async def search(
        self, query: str, *, date_posted: str, country: str = "in", page: int = 1
    ) -> list[dict[str, Any]]:
        """Search job postings; rows use JSearch's field names."""


async def ledger_recorder(provider: str, endpoint: str) -> None:
    """Record one paid call, refusing when today's quota is used.

    Args:
        provider: ``jsearch`` or ``serpapi``.
        endpoint: The API path called.

    Raises:
        QuotaExceeded: Today's quota is already used up.
    """
    from jobpilot.db.queries import api_usage
    from jobpilot.db.session import transaction
    from jobpilot.safety import caps

    async with transaction() as conn:
        # Serialise with other capacity checks so two calls can't both
        # take
        # the last unit of quota.
        await caps.lock_capacity(conn)
        if await caps.api_quota_left(conn) <= 0:
            raise QuotaExceeded("the daily jobs-API quota is used up")
        await api_usage.record(conn, provider=provider, endpoint=endpoint)


class JSearchClient:
    """JSearch (RapidAPI) jobs search over Google for Jobs."""

    provider = "jsearch"

    def __init__(
        self,
        api_key: str,
        host: str,
        transport: httpx.AsyncBaseTransport | None = None,
        recorder: Recorder | None = None,
    ) -> None:
        """Create the client.

        Args:
            api_key: The RapidAPI key.
            host: The JSearch host (JSEARCH_HOST).
            transport: An httpx transport for tests.
            recorder: Records each paid call, or raises when over quota.
        """
        self.host = host
        self.headers = {"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": host}
        self.transport = transport
        self.recorder = recorder
        self.calls = 0

    @classmethod
    def from_settings(cls) -> JSearchClient | None:
        """Build a client from ``.env``, or None when no key is set."""
        s = get_settings()
        if s.jsearch_api_key is None or not s.jsearch_api_key.get_secret_value():
            return None
        return cls(s.jsearch_api_key.get_secret_value(), s.jsearch_host, recorder=ledger_recorder)

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """Make one recorded GET call and return the JSON body.

        Raises:
            QuotaExceeded: Today's quota is used (no call is made).
            JobsApiError: The API answered with an error.
        """
        # Record before calling, so a refused call costs nothing.
        if self.recorder is not None:
            await self.recorder(self.provider, path)
        self.calls += 1
        async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
            resp = await client.get(f"https://{self.host}{path}", params=params, headers=self.headers)
        if resp.status_code == 429:
            raise JobsApiError("jobs API quota or rate limit reached (429)")
        if resp.status_code >= 400:
            raise JobsApiError(f"jobs API error {resp.status_code}: {resp.text[:200]}")
        return dict(resp.json())

    async def search(
        self, query: str, *, date_posted: str, country: str = "in", page: int = 1
    ) -> list[dict[str, Any]]:
        """Search jobs posted within ``date_posted`` (today, 3days, week, ...).

        Args:
            query: The search text, e.g. "data scientist in Bengaluru".
            date_posted: JSearch's posting-date bucket.
            country: Two-letter country code.
            page: The result page.

        Returns:
            Raw job rows.
        """
        body = await self._get(
            "/search-v2",  # JSearch moved search to /search-v2 (data: {"jobs": [...], "cursor": ...})
            {"query": query, "page": page, "num_pages": 1, "date_posted": date_posted, "country": country},
        )
        data = body.get("data") or []
        # search-v2 nests the rows under data.jobs; the old API had a
        # list.
        return list(data.get("jobs") or []) if isinstance(data, dict) else list(data)


_SERP_CHIPS = {"today": "date_posted:today", "3days": "date_posted:3days", "week": "date_posted:week"}
_AGO = re.compile(r"(\d+)\s*(hour|day|week|month)s?\s+ago", re.I)


def _posted_ts(text: str | None) -> float | None:
    """'2 days ago' -> unix timestamp (SerpApi only gives relative posting times)."""
    m = _AGO.search(text or "")
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    delta = {"hour": timedelta(hours=n), "day": timedelta(days=n), "week": timedelta(weeks=n)}.get(
        unit, timedelta(days=30 * n)
    )
    return (clock.now() - delta).timestamp()


class SerpApiClient:
    """Google Jobs through SerpApi, returning JSearch-shaped rows so the source code stays the same."""

    provider = "serpapi"
    ENDPOINT = "https://serpapi.com/search.json"

    def __init__(
        self,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        recorder: Recorder | None = None,
    ) -> None:
        """Create the client.

        Args:
            api_key: The SerpApi key.
            transport: An httpx transport for tests.
            recorder: Records each paid call, or raises when over quota.
        """
        self.api_key = api_key
        self.transport = transport
        self.recorder = recorder
        self.calls = 0

    @classmethod
    def from_settings(cls) -> SerpApiClient | None:
        """Build a client from ``.env``, or None when no key is set."""
        key = get_settings().serpapi_api_key
        if key is None or not key.get_secret_value():
            return None
        return cls(key.get_secret_value(), recorder=ledger_recorder)

    async def search(
        self, query: str, *, date_posted: str, country: str = "in", page: int = 1
    ) -> list[dict[str, Any]]:
        """Search Google Jobs and return rows in JSearch's shape.

        Args:
            query: The search text.
            date_posted: today, 3days or week (others search all dates).
            country: Two-letter country code.
            page: Unused; SerpApi pages with tokens.

        Returns:
            Job rows with JSearch field names.
        """
        if self.recorder is not None:
            await self.recorder(self.provider, "google_jobs")
        self.calls += 1
        params: dict[str, Any] = {"engine": "google_jobs", "q": query, "gl": country, "api_key": self.api_key}
        if chip := _SERP_CHIPS.get(date_posted):
            params["chips"] = chip
        async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
            resp = await client.get(self.ENDPOINT, params=params)
        if resp.status_code >= 400:
            raise JobsApiError(f"SerpApi error {resp.status_code}")
        rows = []
        for item in resp.json().get("jobs_results") or []:
            ext = item.get("detected_extensions") or {}
            options = item.get("apply_options") or []
            rows.append(
                {
                    "job_id": f"serp:{item.get('job_id')}",
                    "job_title": item.get("title"),
                    "employer_name": item.get("company_name"),
                    "job_city": item.get("location"),
                    "job_description": item.get("description"),
                    "job_apply_link": options[0].get("link") if options else item.get("share_link"),
                    "job_posted_at_timestamp": _posted_ts(ext.get("posted_at")),
                    "job_is_remote": bool(ext.get("work_from_home")),
                    "job_publisher": options[0].get("title") if options else None,
                }
            )
        return rows


class FallbackJobsClient:
    """JSearch first; SerpApi when JSearch fails (errors, rate limits). Quota refusals are not retried."""

    def __init__(self, primary: JobsApi | None, fallback: JobsApi | None) -> None:
        """Use ``primary`` first and ``fallback`` when it fails."""
        self.primary, self.fallback = primary, fallback

    @property
    def calls(self) -> int:
        """Paid calls made by both clients together."""
        return sum(c.calls for c in (self.primary, self.fallback) if c is not None)

    @classmethod
    def from_settings(cls) -> FallbackJobsClient | None:
        """Build from whichever clients have keys, or None if neither does."""
        primary, fallback = JSearchClient.from_settings(), SerpApiClient.from_settings()
        return cls(primary, fallback) if primary or fallback else None

    async def _try(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Call ``method`` on the primary, then the fallback on failure.

        Raises:
            QuotaExceeded: The quota is used; the fallback isn't tried.
            JobsApiError: Every configured client failed.
        """
        last: Exception | None = None
        for client in (self.primary, self.fallback):
            if client is None:
                continue
            try:
                return await getattr(client, method)(*args, **kwargs)
            except QuotaExceeded:
                raise
            except (JobsApiError, httpx.HTTPError) as exc:
                log.warning("%s.%s failed (%s); trying the fallback", type(client).__name__, method, exc)
                last = exc
        raise JobsApiError(str(last) if last else "no jobs API configured")

    async def search(
        self, query: str, *, date_posted: str, country: str = "in", page: int = 1
    ) -> list[dict[str, Any]]:
        """Search jobs with the first client that works."""
        return list(await self._try("search", query, date_posted=date_posted, country=country, page=page))
