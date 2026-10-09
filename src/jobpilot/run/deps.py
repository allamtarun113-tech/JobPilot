"""What a run needs from the outside world.

Built once by the worker process (src/jobpilot/worker/main.py) and
replaced with fakes in tests. Kept out of the checkpointed run state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jobpilot.integrations.jobs_api import JobsApi
from jobpilot.integrations.mcp_client import MCPConnection
from jobpilot.integrations.vectors import VectorStore
from jobpilot.llm.client import LLMClient
from jobpilot.llm.embeddings import Embedder
from jobpilot.models.preferences import Preferences
from jobpilot.sources.base import JobSource
from jobpilot.sources.enrich_lookup import EnrichLookup
from jobpilot.sources.finder_agent import JobFinderAgent
from jobpilot.sources.job_finder import JobFinderSource
from jobpilot.sources.jobs_api import JobsApiSource


@dataclass
class RunDeps:
    """Clients a run uses: embeddings, Qdrant, the model, jobs API, agent."""

    embedder: Embedder
    vectors: VectorStore
    llm: LLMClient | None
    jobs_api: JobsApi | None
    enrich: EnrichLookup
    web: MCPConnection | None = None
    agent: JobFinderAgent | None = None
    extra_sources: list[Any] = field(default_factory=list)

    def sources(self, prefs: Preferences, run_id: int | None = None) -> list[JobSource]:
        """Return the job sources to fetch, in order."""
        return [
            JobsApiSource(self.jobs_api, prefs),
            JobFinderSource(self.agent, prefs, run_id),
            *self.extra_sources,
        ]


_deps: RunDeps | None = None


def set_deps(deps: RunDeps) -> None:
    """Install the process-wide run dependencies (worker start-up, tests)."""
    global _deps
    _deps = deps


def get_deps() -> RunDeps:
    """Return the installed run dependencies.

    Raises:
        RuntimeError: The worker hasn't set them yet.
    """
    if _deps is None:
        raise RuntimeError("run dependencies are not configured (the worker sets them at start-up)")
    return _deps
