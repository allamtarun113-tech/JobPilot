"""Prep Research agent: public interview-experience pages for one job.

Reads public interview-experience pages for one company and role and
returns structured notes with their source links. It runs on the shared
AgentRuntime (caps per Prepare request, a span per step); a note is
kept only if its source is a page it actually searched for and fetched.
"""

from __future__ import annotations

import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from jobpilot.integrations.mcp_client import MCPConnection
from jobpilot.llm import prompts
from jobpilot.llm.agent_runtime import AgentLimit, AgentRuntime
from jobpilot.llm.client import LLMClient
from jobpilot.models.prep import ResearchNote, Topic
from jobpilot.safety import sanitize


class Decision(BaseModel):
    """The research agent's next move: search, fetch, or finish with notes."""

    action: Literal["search", "fetch", "finish"]
    query: str | None = None
    url: str | None = None
    notes: list[ResearchNote] = Field(default_factory=list, max_length=20)


class PrepResearchAgent:
    """The bounded Prep Research agent."""

    def __init__(self, llm: LLMClient, web: MCPConnection) -> None:
        """Create the agent on the shared runtime (``prep_research`` caps)."""
        self.runtime = AgentRuntime(llm, web, agent="prep_research")

    async def run(self, company: str, title: str, topics: list[Topic]) -> list[ResearchNote]:
        """Research interview experiences for a company and role.

        Args:
            company: The company.
            title: The role title.
            topics: The prep topics to look for.

        Returns:
            Unique notes, each from a page the agent really fetched.
        """
        key = f"prep:{uuid4().hex}"  # caps are per Prepare request
        observations: list[dict] = []
        result_urls: set[str] = set()
        fetched_urls: set[str] = set()
        collected: list[ResearchNote] = []

        def prompt(instruction: str = "") -> prompts.RenderedPrompt:
            """Render the research prompt with the observations so far (fenced)."""
            return prompts.render(
                "agents/prep_research",
                company=company,
                title=title,
                topics=json.dumps([topic.model_dump() for topic in topics]),
                observations=sanitize.fence("web-observations", json.dumps(observations)[-30000:]),
                instruction=instruction,
            )

        try:
            while True:
                decision = await self.runtime.decide(prompt(), Decision, run_id=key, purpose="research")
                collected += [n for n in decision.notes if n.source_url in fetched_urls]
                if decision.action == "finish":
                    break
                if decision.action == "search":
                    if not decision.query:
                        observations.append({"error": "search needs a query"})
                        continue
                    results = await self.runtime.search(decision.query, run_id=key, count=8, company=company)
                    result_urls.update(str(item["url"]) for item in results if item.get("url"))
                    observations.append({"search": decision.query, "results": results})
                    continue
                if decision.url not in result_urls:
                    observations.append({"url": decision.url, "error": "only search results can be fetched"})
                    continue
                try:
                    page = await self.runtime.fetch(str(decision.url), run_id=key)
                except RuntimeError as exc:
                    observations.append({"url": decision.url, "error": str(exc)})
                    continue
                fetched_urls.add(str(decision.url))
                observations.append(
                    {"url": decision.url, "title": page.get("title"), "text": str(page.get("text"))[:12000]}
                )
        except AgentLimit:
            pass  # caps reached: keep the notes gathered so far
        try:
            # Pages were read but no notes written: ask once for the
            # notes.
            if fetched_urls and not collected:  # pages were read but no notes written yet: one last summary
                final = await self.runtime.decide(
                    prompt("Now return action 'finish' with notes from every useful fetched page."),
                    Decision,
                    run_id=f"{key}:notes",
                    purpose="research-notes",
                )
                collected += [n for n in final.notes if n.source_url in fetched_urls]
        except AgentLimit:
            pass
        finally:
            self.runtime.forget(key)
            self.runtime.forget(f"{key}:notes")
        unique: dict[tuple[str, str], ResearchNote] = {}
        for note in collected:
            unique.setdefault((note.source_url, note.note), note)
        return list(unique.values())
