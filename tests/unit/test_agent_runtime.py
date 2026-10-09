"""Per-run agent caps are enforced before model and MCP calls."""

from types import SimpleNamespace
from typing import Any

import pytest

from jobpilot.llm.agent_runtime import AgentLimit, AgentRuntime
from jobpilot.llm.prompts import RenderedPrompt
from jobpilot.sources.finder_agent import Decision


class FakeLLM:
    """A fake model that always finishes, using 3 tokens per call."""

    async def structured(self, *args: Any, **kwargs: Any) -> Any:
        """Return a finish decision with token counts."""
        return SimpleNamespace(
            output=Decision(action="finish"),
            input_tokens=2,
            output_tokens=1,
            cache_read_tokens=0,
            cache_write_tokens=0,
        )


class FakeWeb:
    """A fake web MCP connection that records tool calls."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.calls: list[str] = []

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Record the tool and return an empty search result or page."""
        self.calls.append(tool)
        if tool == "search":
            return {"ok": True, "results": []}
        return {"ok": True, "page": {"url": arguments["url"], "title": "", "text": ""}}


async def test_step_caps_are_per_task_but_tokens_are_per_run() -> None:
    """Steps are capped per task; tokens across the whole run."""
    runtime = AgentRuntime(FakeLLM(), FakeWeb())  # type: ignore[arg-type]
    runtime.cfg = {
        "max_steps_per_task": 1,
        "max_searches_per_run": 1,
        "max_pages_per_run": 1,
        "max_pages_per_lead": 1,
        "max_tokens_per_run": 7,
    }
    prompt = RenderedPrompt("x", 1, "system", "user")
    await runtime.decide(prompt, Decision, run_id=9, purpose="find_leads")
    with pytest.raises(AgentLimit, match="step"):
        await runtime.decide(prompt, Decision, run_id=9, purpose="find_leads")
    await runtime.decide(prompt, Decision, run_id=9, purpose="find_full_jd")
    with pytest.raises(AgentLimit, match="token"):
        await runtime.decide(prompt, Decision, run_id=9, purpose="another_task")


async def test_tool_caps_stop_calls_before_the_boundary() -> None:
    """Search and page caps refuse the call before it is made."""
    web = FakeWeb()
    runtime = AgentRuntime(FakeLLM(), web)  # type: ignore[arg-type]
    runtime.cfg = {
        "max_steps_per_task": 1,
        "max_searches_per_run": 1,
        "max_pages_per_run": 1,
        "max_pages_per_lead": 1,
        "max_tokens_per_run": 10,
    }
    await runtime.search("one", run_id=1)
    with pytest.raises(AgentLimit, match="search"):
        await runtime.search("two", run_id=1)
    await runtime.fetch("https://example.com", run_id=1)
    with pytest.raises(AgentLimit, match="page"):
        await runtime.fetch("https://example.org", run_id=1)
    assert web.calls == ["search", "fetch"]
