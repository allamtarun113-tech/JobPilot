"""In-flight run state (checkpointed by LangGraph only while the run is executing)."""

from __future__ import annotations

from typing import TypedDict


class RunState(TypedDict, total=False):
    """The checkpointed state of one run, passed between graph steps."""

    run_id: int
    holder: str
    card_cap: int
    new_job_ids: list[int]  # jobs first seen this run (after dedup)
    pending_job_ids: list[int]  # survivors of hard filters + stage A, best first, still to process
    cards_made: int
    jobs_processed: int
    consecutive_errors: int
    counts: dict[str, dict[str, int]]  # per source: fetched / new / dupes ...; "pipeline": per outcome
    notes: list[str]
    preflight: dict[str, str]
    stop_reason: str | None
    resume_at: str | None  # after /pause: the node a /resume continues from
