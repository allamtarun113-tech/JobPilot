"""The cheap full-JD lookup, tried before the Job Finder agent.

In order: the posting's own careers-system API when the lead links to
one (Lever, Greenhouse, Ashby), the company's careers system found by
name (Greenhouse and Lever public job boards), and the jobs API. Only
fixed API hosts are called. A JD is accepted only if the title matches
(and the company, when found by name).
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from jobpilot.integrations.jobs_api import JobsApi, JobsApiError
from jobpilot.reference import normalize

log = logging.getLogger(__name__)


def _strip_html(text: str) -> str:
    """Turn HTML into plain text, keeping line breaks at block ends."""
    text = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", html.unescape(text), flags=re.I)
    return re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def title_matches(a: str, b: str) -> bool:
    """Return True if two job titles share at least 75% of their words.

    Measured against the shorter title, after normalizing both.
    """
    ta, tb = set(normalize.title_norm(a).split()), set(normalize.title_norm(b).split())
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= 0.75


_SAFE_PART = re.compile(r"[A-Za-z0-9_-]{1,100}")  # a slug or id taken from the lead's link


def _lever_text(posting: dict[str, Any]) -> str:
    """Return a Lever posting's description plus its titled lists."""
    text = posting.get("descriptionPlain") or _strip_html(posting.get("description", ""))
    lists = "\n".join(
        f"{x.get('text')}: {_strip_html(x.get('content', ''))}" for x in posting.get("lists", [])
    )
    return f"{text}\n{lists}".strip()


def _slugs(company: str) -> list[str]:
    """Return likely board slugs for a company ("acme labs" -> acmelabs, acme-labs)."""
    norm = normalize.company_norm(company)
    return list(dict.fromkeys([norm.replace(" ", ""), norm.replace(" ", "-")])) if norm else []


class EnrichLookup:
    """Looks up a job's full JD through public careers-system APIs."""

    def __init__(self, jobs_api: JobsApi | None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """Create the lookup.

        Args:
            jobs_api: The jobs API client for the last fallback, or
                None.
            transport: An httpx transport for tests.
        """
        self.jobs_api = jobs_api
        self.transport = transport

    async def find(self, job: dict[str, Any]) -> tuple[str, str] | None:
        """(full JD text, where it came from) or None."""
        for finder in (self._from_url, self._greenhouse, self._lever, self._jobs_api):
            try:
                found = await finder(job)
            except (httpx.HTTPError, JobsApiError, ValueError, KeyError) as exc:
                log.info("JD lookup via %s failed for job %s: %s", finder.__name__, job["id"], exc)
                continue
            if found:
                return found
        return None

    async def _get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        """GET a JSON API (no redirects); None for any non-200 answer."""
        async with httpx.AsyncClient(timeout=15, transport=self.transport, follow_redirects=False) as client:
            resp = await client.get(url, params=params)
        if resp.status_code != 200:
            return None
        return resp.json()

    async def _from_url(self, job: dict[str, Any]) -> tuple[str, str] | None:
        """The posting the lead links to, read through its careers system's public API (no page fetch)."""
        url = str(job.get("apply_url") or "")
        host = (urlsplit(url).hostname or "").lower()
        parts = [p for p in urlsplit(url).path.split("/") if p]
        # Path parts are put into API URLs: only plain slugs and ids.
        if not all(_SAFE_PART.fullmatch(p) for p in parts[:4]):
            return None
        if host == "jobs.lever.co" and len(parts) >= 2:
            posting = await self._get_json(f"https://api.lever.co/v0/postings/{parts[0]}/{parts[1]}")
            if posting and title_matches(posting.get("text", ""), job["title"]):
                return _lever_text(posting), f"lever:{parts[0]}"
        if host in ("boards.greenhouse.io", "job-boards.greenhouse.io") and "jobs" in parts:
            i = parts.index("jobs")
            if i >= 1 and i + 1 < len(parts) and parts[i + 1].isdigit():
                posting = await self._get_json(
                    f"https://boards-api.greenhouse.io/v1/boards/{parts[i - 1]}/jobs/{parts[i + 1]}"
                )
                if posting and title_matches(posting.get("title", ""), job["title"]):
                    return _strip_html(posting.get("content", "")), f"greenhouse:{parts[i - 1]}"
        if host == "jobs.ashbyhq.com" and len(parts) >= 2:
            board = await self._get_json(f"https://api.ashbyhq.com/posting-api/job-board/{parts[0]}")
            for posting in (board or {}).get("jobs", []):
                if posting.get("id") == parts[1] and title_matches(posting.get("title", ""), job["title"]):
                    text = posting.get("descriptionPlain") or _strip_html(posting.get("descriptionHtml", ""))
                    return text, f"ashby:{parts[0]}"
        return None

    async def _greenhouse(self, job: dict[str, Any]) -> tuple[str, str] | None:
        """Search the company's Greenhouse board by name for this title."""
        for slug in _slugs(job["company"]):
            data = await self._get_json(
                f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", {"content": "true"}
            )
            for posting in (data or {}).get("jobs", []):
                if title_matches(posting.get("title", ""), job["title"]):
                    return _strip_html(posting.get("content", "")), f"greenhouse:{slug}"
        return None

    async def _lever(self, job: dict[str, Any]) -> tuple[str, str] | None:
        """Search the company's Lever board by name for this title."""
        for slug in _slugs(job["company"]):
            data = await self._get_json(f"https://api.lever.co/v0/postings/{slug}", {"mode": "json"})
            for posting in data or []:
                if title_matches(posting.get("text", ""), job["title"]):
                    return _lever_text(posting), f"lever:{slug}"
        return None

    async def _jobs_api(self, job: dict[str, Any]) -> tuple[str, str] | None:
        """Search the jobs API for this title at this company."""
        if self.jobs_api is None:
            return None
        for item in await self.jobs_api.search(f"{job['title']} {job['company']}", date_posted="month"):
            same_company = normalize.company_norm(item.get("employer_name") or "") == job["company_norm"]
            if (
                same_company
                and title_matches(item.get("job_title") or "", job["title"])
                and item.get("job_description")
            ):
                return item["job_description"], "jobs_api"
        return None
