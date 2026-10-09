"""Full-JD lookup for the jobs that survive dedup and the filters.

The cheap lookup first, the Job Finder agent only if it fails. Results
live on the job row, keyed by its board job id.
"""

from __future__ import annotations

from typing import Any, Protocol

from jobpilot.sources.enrich_lookup import EnrichLookup


class AgentLookup(Protocol):
    """The part of the Job Finder agent the JD lookup needs."""

    async def find(self, job: dict[str, Any], run_id: int | None = None) -> tuple[str, str] | None:
        """Return (full JD, where found) for a job, or None."""


async def find_full_jd(
    job: dict[str, Any], cheap: EnrichLookup, agent: AgentLookup | None, run_id: int | None = None
) -> tuple[str, str] | None:
    """Find a job's full JD: the cheap lookup first, the agent only if it fails.

    Args:
        job: The job row.
        cheap: The careers-system / jobs-API lookup.
        agent: The Job Finder agent, or None when unavailable.
        run_id: The run whose agent caps apply.

    Returns:
        (JD text, where found), or None when nothing of at least 200
        characters was found.
    """
    found = await cheap.find(job)
    if found is None and agent is not None:
        found = await agent.find(job, run_id)
    if found and len(found[0]) >= 200:
        return found
    return None
