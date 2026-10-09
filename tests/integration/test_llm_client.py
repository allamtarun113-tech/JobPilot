"""LLM client with a fake OpenAI API: pinned model, one retry on invalid output, accounting."""

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI
from sqlalchemy import select

from jobpilot.core.config import get_config
from jobpilot.core.errors import LLMInvalidOutput, LLMRefusal
from jobpilot.db.session import transaction
from jobpilot.db.tables import llm_usage
from jobpilot.llm import prompts
from jobpilot.llm.client import LLMClient
from jobpilot.models.health import PingResult
from jobpilot.models.job_chat import JobChatDecision

pytestmark = pytest.mark.usefixtures("db")


class FakeMessages:
    """A fake Responses API returning queued outputs."""

    def __init__(self, outputs: list[Any], stop_reason: str = "end_turn") -> None:
        """Return these outputs in order (None = invalid JSON)."""
        self.outputs = list(outputs)
        self.stop_reason = stop_reason
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        """Record the request and answer with the next output."""
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_text=(value.model_dump_json() if (value := self.outputs.pop(0)) is not None else "{}"),
            output=(
                [SimpleNamespace(content=[SimpleNamespace(type="refusal")])]
                if self.stop_reason == "refusal"
                else []
            ),
            model=kwargs["model"],
            status="incomplete" if self.stop_reason == "incomplete" else "completed",
            usage=SimpleNamespace(
                input_tokens=100, output_tokens=10, input_tokens_details=SimpleNamespace(cached_tokens=50)
            ),
        )


def client(fake: FakeMessages) -> LLMClient:
    """Return an LLM client that talks to the fake API."""
    return LLMClient(api=SimpleNamespace(responses=fake))  # type: ignore[arg-type]


async def test_structured_call_uses_pinned_model_and_records_usage() -> None:
    """A call uses the pinned model, strict schema and records cost."""
    fake = FakeMessages([PingResult(ok=True, echo="pilot")])
    result = await client(fake).structured(
        "health_ping", prompts.render("system/ping", word="pilot"), PingResult
    )
    assert result.output.echo == "pilot"
    call = fake.calls[0]
    assert call["model"] == "gpt-4o-mini"
    assert "reasoning" not in call and call["max_output_tokens"] == 16000  # not a reasoning model
    assert call["text"]["format"]["strict"] is True
    assert call["text"]["format"]["schema"]["additionalProperties"] is False
    assert call["store"] is False
    assert result.input_tokens == 50 and result.cache_read_tokens == 50
    assert result.cost_usd == pytest.approx((50 * 0.15 + 50 * 0.075 + 10 * 0.6) / 1e6)
    async with transaction() as conn:
        rows = (await conn.execute(select(llm_usage))).mappings().all()
    assert len(rows) == 1 and rows[0]["prompt_version"] == "system/ping_v1" and rows[0]["valid_output"]


async def test_all_tasks_use_openai() -> None:
    """Every task goes to the configured OpenAI model."""
    fake = FakeMessages([PingResult(ok=True, echo="x")])
    await client(fake).structured("tailor", prompts.render("system/ping", word="x"), PingResult)
    assert fake.calls[0]["model"] == "gpt-4o-mini"
    assert "extra_body" not in fake.calls[0]


async def test_invalid_output_is_retried_once_then_fails() -> None:
    """Invalid output is retried once, then fails."""
    fake = FakeMessages([None, PingResult(ok=True, echo="ok")])
    result = await client(fake).structured(
        "health_ping", prompts.render("system/ping", word="ok"), PingResult
    )
    assert result.output.ok and len(fake.calls) == 2

    fake = FakeMessages([None, None])
    with pytest.raises(LLMInvalidOutput):
        await client(fake).structured("health_ping", prompts.render("system/ping", word="ok"), PingResult)
    assert len(fake.calls) == 2


async def test_refusal_is_not_retried() -> None:
    """A refusal fails at once without a retry."""
    fake = FakeMessages([None], stop_reason="refusal")
    with pytest.raises(LLMRefusal):
        await client(fake).structured("health_ping", prompts.render("system/ping", word="ok"), PingResult)
    assert len(fake.calls) == 1


async def test_incomplete_output_is_retried_and_accounted() -> None:
    """Incomplete responses are retried and both calls are recorded."""
    fake = FakeMessages([PingResult(ok=True, echo="x"), PingResult(ok=True, echo="x")], "incomplete")
    with pytest.raises(LLMInvalidOutput):
        await client(fake).structured("health_ping", prompts.render("system/ping", word="x"), PingResult)
    async with transaction() as conn:
        rows = (await conn.execute(select(llm_usage))).mappings().all()
    assert len(fake.calls) == len(rows) == 2
    assert all(not row["valid_output"] for row in rows)


async def test_real_sdk_wire_request_and_chat_proposal_decoding() -> None:
    """The real SDK request is well formed and null proposal keys are dropped."""
    requests: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Check the request and answer like the Responses API."""
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path == "/v1/responses"
        assert request.headers["authorization"] == "Bearer offline-test-key"
        decision = JobChatDecision(
            action="change_prefs",
            reply="Confirm this change",
            proposal={"field": "location", "value": "remote"},
        )
        data = decision.model_dump()
        data["proposal"].update(item_id=None, text=None, action=None)
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 0,
                "model": "gpt-4o-mini-2024-07-18",
                "status": "completed",
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": json.dumps(data), "annotations": []}],
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                    "input_tokens_details": {"cached_tokens": 40},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        async with AsyncOpenAI(api_key="offline-test-key", http_client=http) as api:
            result = await LLMClient(api).structured(
                "job_chat", prompts.render("system/ping", word="x"), JobChatDecision
            )
    assert result.output.proposal == {"field": "location", "value": "remote"}
    assert result.input_tokens == 60 and result.cache_read_tokens == 40
    assert result.cost_usd == pytest.approx((60 * 0.15 + 40 * 0.075 + 20 * 0.6) / 1e6)
    assert requests[0]["store"] is False
    assert requests[0]["model"] == "gpt-4o-mini" and "reasoning" not in requests[0]
    assert requests[0]["text"]["format"]["strict"] is True
    assert [item["role"] for item in requests[0]["input"]] == ["system", "user"]


async def test_a_reasoning_model_gets_its_effort_and_output_headroom(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a task is pointed at a gpt-5 model, it sends the configured effort and extra output room."""
    cfg = get_config()
    monkeypatch.setitem(cfg.models, "resume_composer", "gpt-5-nano")
    monkeypatch.setitem(cfg.models, "tailor", "gpt-5-nano")
    fake = FakeMessages([PingResult(ok=True, echo="x"), PingResult(ok=True, echo="x")])
    await client(fake).structured("resume_composer", prompts.render("system/ping", word="x"), PingResult)
    await client(fake).structured("tailor", prompts.render("system/ping", word="x"), PingResult)
    assert fake.calls[0]["reasoning"] == {"effort": "medium"}
    assert (
        fake.calls[1]["reasoning"] == {"effort": "low"} and fake.calls[1]["max_output_tokens"] == 16000 + 8000
    )
