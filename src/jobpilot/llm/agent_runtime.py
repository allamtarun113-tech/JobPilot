"""Shared bounded runtime for the agents that choose read-only MCP calls (Job Finder, Prep Research).

Limits are enforced before each model or MCP action: steps per task, and searches, pages and tokens per run
(config agents.<name>). Every step and tool call is a span. Search results from preferred careers systems
(config fetch_rules.preferred_careers_domains) and the company's own site are listed first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeVar
from urllib.parse import urlsplit

from opentelemetry import trace
from pydantic import BaseModel

from jobpilot.core.config import get_config, load_yaml
from jobpilot.integrations.mcp_client import MCPConnection
from jobpilot.llm.client import LLMClient
from jobpilot.llm.prompts import RenderedPrompt

T = TypeVar("T", bound=BaseModel)
tracer = trace.get_tracer("jobpilot.agents")
UsageKey = int | str


class AgentLimit(RuntimeError):
    """A per-run cap (searches, pages, tokens) is reached: stop for this run."""


class StepLimit(AgentLimit):
    """The step cap for this one task is reached: the agent tried and gave up on this task."""


@dataclass
class AgentUsage:
    """What one agent has used so far in one run (or one task key)."""

    steps: int = 0
    task_steps: dict[str, int] = field(default_factory=dict)
    searches: int = 0
    pages: int = 0
    tokens: int = 0


def preferred_domains() -> tuple[str, ...]:
    """Return the careers-system domains listed in ``fetch_rules.yaml``."""
    return tuple(str(d).lower() for d in load_yaml("fetch_rules.yaml").get("preferred_careers_domains", []))


def rank_results(results: list[dict[str, str]], company: str | None = None) -> list[dict[str, str]]:
    """Careers systems and the company's own domain first; otherwise keep the search engine's order."""
    preferred = preferred_domains()
    # Company name as bare letters and digits, to spot it in a host
    # name.
    slug = "".join(ch for ch in (company or "").lower() if ch.isalnum())

    def rank(item: dict[str, str]) -> int:
        """Return 0 for a careers system, 1 for the company's site, else 2."""
        host = (urlsplit(str(item.get("url") or "")).hostname or "").lower()
        if any(host == d or host.endswith(f".{d}") for d in preferred):
            return 0
        if slug and slug in host.replace("-", "").replace(".", ""):
            return 1
        return 2

    # sorted() is stable, so equal ranks keep the search engine's order.
    return sorted(results, key=rank)


class AgentRuntime:
    """Enforces per-run limits before each model or MCP action and traces every step."""

    def __init__(self, llm: LLMClient, web: MCPConnection, agent: str = "job_finder") -> None:
        """Set up the runtime for one agent.

        Args:
            llm: The model client.
            web: A read-only web MCP connection (search and fetch).
            agent: The agent's name; its caps come from
                ``config.agents``.
        """
        self.llm = llm
        self.web = web
        self.agent = agent
        self.cfg = get_config().agents[agent]
        self._usage: dict[UsageKey, AgentUsage] = {}

    def usage(self, key: UsageKey | None) -> AgentUsage:
        """Return (creating if needed) the usage counters for a run or task."""
        return self._usage.setdefault(key or 0, AgentUsage())

    def forget(self, key: UsageKey) -> None:
        """Drop the usage counters kept for ``key``."""
        self._usage.pop(key, None)

    async def decide(
        self, prompt: RenderedPrompt, output: type[T], *, run_id: UsageKey | None, purpose: str
    ) -> T:
        """Ask the model for the agent's next structured decision.

        Checks the per-task step cap and the per-run token cap first,
        and
        again after the call, since a call may overshoot.

        Args:
            prompt: The rendered prompt.
            output: The Pydantic schema the model must return.
            run_id: The run (or task key) the usage counts against.
            purpose: The task name; steps are capped per purpose.

        Returns:
            The validated model output.

        Raises:
            StepLimit: This task used all its steps.
            AgentLimit: The run's token cap is reached.
        """
        use = self.usage(run_id)
        purpose_steps = use.task_steps.get(purpose, 0)
        if purpose_steps >= self.cfg["max_steps_per_task"]:
            raise StepLimit("agent step cap reached")
        remaining = self.cfg["max_tokens_per_run"] - use.tokens
        if remaining <= 0:
            raise AgentLimit("agent token cap reached")
        use.steps += 1
        use.task_steps[purpose] = purpose_steps + 1
        with tracer.start_as_current_span(
            "agent.step",
            attributes={
                "agent.name": self.agent,
                "agent.purpose": purpose,
                "agent.step": use.task_steps[purpose],
            },
        ) as span:
            result = await self.llm.structured(
                self.agent,
                prompt,
                output,
                # Only real run ids are recorded against runs; other
                # keys are
                # task-local counters.
                run_id=run_id if isinstance(run_id, int) and run_id > 0 else None,
                # Never ask for more output tokens than the run has
                # left.
                max_tokens=min(4096, remaining),
            )
            used = sum(
                int(getattr(result, name, 0) or 0)
                for name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
            )
            use.tokens += used
            span.set_attributes({"agent.tokens": used, "agent.tokens_total": use.tokens})
            if use.tokens > self.cfg["max_tokens_per_run"]:
                raise AgentLimit("agent token cap reached")
            return result.output

    async def search(
        self, query: str, *, run_id: UsageKey | None, count: int = 10, company: str | None = None
    ) -> list[dict[str, str]]:
        """Run one web search through the MCP server, within the search cap.

        Args:
            query: The search query.
            run_id: The run (or task key) the search counts against.
            count: How many results to ask for.
            company: The company, so its own site can be ranked first.

        Returns:
            Search results (title, url, snippet), best sources first.

        Raises:
            AgentLimit: The run's search cap is reached.
            RuntimeError: The search failed.
        """
        use = self.usage(run_id)
        if use.searches >= self.cfg["max_searches_per_run"]:
            raise AgentLimit("agent search cap reached")
        use.searches += 1
        with tracer.start_as_current_span(
            "agent.tool", attributes={"agent.name": self.agent, "agent.tool": "search"}
        ):
            result = await self.web.call("search", {"query": query, "count": count})
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or "web search failed"))
        return rank_results(list(result.get("results") or []), company)

    async def fetch(self, url: str, *, run_id: UsageKey | None) -> dict[str, str | int]:
        """Fetch one page through the MCP server, within the page cap.

        The server applies the SSRF guard and the deny-list.

        Args:
            url: The page to fetch.
            run_id: The run (or task key) the fetch counts against.

        Returns:
            The page (url, title, text, status).

        Raises:
            AgentLimit: The run's page cap is reached.
            RuntimeError: The fetch failed or was refused.
        """
        use = self.usage(run_id)
        if use.pages >= self.cfg["max_pages_per_run"]:
            raise AgentLimit("agent page cap reached")
        use.pages += 1
        with tracer.start_as_current_span(
            "agent.tool", attributes={"agent.name": self.agent, "agent.tool": "fetch"}
        ):
            result = await self.web.call("fetch", {"url": url})
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or "web fetch failed"))
        return dict(result.get("page") or {})
