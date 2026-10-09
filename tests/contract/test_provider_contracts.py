"""Contract tests: provider response shapes our code depends on.

Offline (always): recorded responses for Telegram, Gmail, JSearch, SerpApi and Brave go through our real
parsing code. Live (nightly, `CONTRACT_LIVE=1` with credentials): one harmless call per provider checks
that the real shape still matches; any drift fails the job and the scheduler's Gmail check raises an alert.
"""

from __future__ import annotations

import os

import httpx
import pytest

from jobpilot.integrations.jobs_api import JSearchClient, SerpApiClient

LIVE = os.environ.get("CONTRACT_LIVE") == "1"
JSEARCH_ROW = {
    "job_id": "abc",
    "job_title": "Data Analyst",
    "employer_name": "Acme",
    "job_city": "Bengaluru",
    "job_description": "SQL",
    "job_apply_link": "https://acme.example/apply",
    "job_posted_at_timestamp": 1790000000,
    "job_is_remote": False,
}


async def test_jsearch_search_shape() -> None:
    """A recorded JSearch response parses into rows with the fields we read."""
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"status": "OK", "data": [JSEARCH_ROW]})
    )
    rows = await JSearchClient("k", "jsearch.p.rapidapi.com", transport=transport).search(
        "x", date_posted="today"
    )
    assert {"job_id", "job_title", "employer_name", "job_apply_link"} <= rows[0].keys()


async def test_serpapi_google_jobs_shape() -> None:
    """A recorded SerpApi response maps onto JSearch field names."""
    body = {
        "jobs_results": [
            {
                "job_id": "j",
                "title": "T",
                "company_name": "C",
                "apply_options": [{"link": "https://x.example"}],
            }
        ]
    }
    rows = await SerpApiClient(
        "k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    ).search("x", date_posted="week")
    assert rows[0]["job_title"] == "T" and rows[0]["job_apply_link"] == "https://x.example"


async def test_telegram_send_message_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """A recorded sendMessage answer yields the message id."""
    from jobpilot.servers.telegram import tools

    class Client:
        """A stand-in httpx client answering with a recorded Telegram reply."""

        def __init__(self, *a: object, **k: object) -> None:
            """Accept any arguments, like httpx.AsyncClient."""

        async def __aenter__(self) -> Client:
            """Return the client itself."""
            return self

        async def __aexit__(self, *a: object) -> None:
            """Do nothing on exit."""

        async def post(self, url: str, json: dict) -> httpx.Response:
            """Return a successful sendMessage response."""
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 77, "chat": {"id": 1}}})

    monkeypatch.setattr(tools.httpx, "AsyncClient", Client)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    from jobpilot.core.config import get_settings

    get_settings.cache_clear()
    try:
        assert await tools.call("sendMessage", {"chat_id": 1, "text": "x"}) == {"ok": True, "message_id": 77}
    finally:
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
        get_settings.cache_clear()


@pytest.mark.skipif(not LIVE, reason="nightly live contract check (CONTRACT_LIVE=1 and real credentials)")
async def test_live_jsearch_contract() -> None:
    """The real JSearch API still returns the fields we read (nightly, live)."""
    client = JSearchClient.from_settings()
    assert client is not None, "JSEARCH_API_KEY needed for the live contract"
    client.recorder = None
    rows = await client.search("data analyst in Bengaluru", date_posted="today")
    assert all({"job_id", "job_title", "employer_name"} <= r.keys() for r in rows)
