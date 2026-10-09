"""Settings parsing, preference limits, the Gmail client and prep quality gates."""

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from evals.runners import prep
from jobpilot.core.config import Settings, get_settings
from jobpilot.models.preferences import Preferences
from jobpilot.models.prep import Question, QuestionSet
from jobpilot.servers.gmail.tools import GmailApi


def test_blank_chat_id_is_optional(monkeypatch):
    """An empty chat id is None; a non-number is an error."""
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    assert Settings(_env_file=None).telegram_chat_id is None
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "invalid")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "values",
    [
        {"emails_per_day": -1},
        {"emails_per_day": 21},
        {"cards_per_day": 0},
        {"cards_per_day": 51},
        {"threshold": 101},
        {"exp_min_years": -1},
        {"notice_period_days": -1},
    ],
)
def test_dashboard_preferences_reject_out_of_range_values(values):
    """Out-of-range preference values are refused."""
    with pytest.raises(ValidationError):
        Preferences(**values)


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_gmail_refreshes_once_on_401_and_retries(monkeypatch, method):
    """A 401 triggers one token refresh and one retry."""
    api = GmailApi("fixture", "fixture", "fixture")
    refreshes = []
    requests = []

    def handler(request):
        """Refuse the first token; accept the refreshed one."""
        if request.url.host == "oauth2.googleapis.com":
            refreshes.append(request)
            return httpx.Response(200, json={"access_token": f"token-{len(refreshes)}", "expires_in": 3600})
        requests.append(request)
        return httpx.Response(
            401 if request.headers["authorization"] == "Bearer token-1" else 200, json={"id": "success"}
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler))
    )
    response = (
        await api._get("/profile") if method == "GET" else await api._post("/drafts/send", {"id": "d1"})
    )
    assert response.status_code == 200 and len(refreshes) == 2
    assert [request.method for request in requests] == [method, method]


async def test_gmail_expiry_and_concurrent_refresh(monkeypatch):
    """An expired token is refreshed once, even by concurrent callers."""
    api = GmailApi("fixture", "fixture", "fixture")
    now = [100.0]
    monkeypatch.setattr("jobpilot.servers.gmail.tools.time.monotonic", lambda: now[0])
    calls = []

    class Client:
        """A fake HTTP client that issues numbered tokens."""

        async def post(self, *args, **kwargs):
            """Return the next token, yielding first so callers overlap."""
            await asyncio.sleep(0)
            calls.append(None)
            return httpx.Response(200, json={"access_token": f"token-{len(calls)}", "expires_in": 100})

    client = Client()
    assert await api._token(client) == "token-1"
    now[0] = 141.0
    tokens = await asyncio.gather(*(api._token(client) for _ in range(4)))
    assert tokens == ["token-2"] * 4 and len(calls) == 2


async def test_prep_quality_gate_rejects_plausible_but_unsupported_answer():
    """Cited but unsupported answers fail the held-out gate."""

    async def fabricated(case):
        """Answer with a confident but unsupported claim."""
        return QuestionSet(
            questions=[
                Question(
                    id="q",
                    category="technical",
                    topic=case["topic"],
                    question="Question",
                    answer="Guaranteed constant time. Acme has three rounds.",
                    citations=[f"chunk:{case['id']}"],
                )
            ]
        )

    summary, failures = await prep.quality(fabricated)
    assert summary["held_out_pass_rate"] == 0 and len(failures) == 5


async def test_held_out_quality_gate_accepts_fixture_answers_and_correct_sources():
    """The expected answers with their source pass the gate."""

    async def supported(case):
        """Answer with the case's expected text."""
        return QuestionSet(
            questions=[
                Question(
                    id="q",
                    category="technical",
                    topic=case["topic"],
                    question="Question",
                    answer=case["expected"],
                    citations=[f"chunk:{case['id']}"],
                )
            ]
        )

    summary, failures = await prep.quality(supported)
    assert summary["held_out_pass_rate"] == 1 and not failures
    assert len(json.loads(prep.DATASET.read_text())) == 5
    assert not get_settings().openai_api_key


async def test_gmail_ambiguous_network_failure_never_replays_send(monkeypatch):
    """A send that timed out is not retried by the client."""
    api = GmailApi("fixture", "fixture", "fixture")
    calls = []

    def handler(request):
        """Issue a token, then time out every API call."""
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        calls.append(request)
        raise httpx.ReadTimeout("acceptance unknown", request=request)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler))
    )
    with pytest.raises(httpx.ReadTimeout):
        await api.send_draft("draft")
    assert len(calls) == 1
