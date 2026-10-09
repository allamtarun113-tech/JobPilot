"""A full JD arrived for a job that was carded from a snippet.

The job is re-extracted and re-scored like any other (pipeline). Then:
- if the card is still waiting for you (new / resume approved) and the score moved materially, a new
  resume version is tailored from the full JD and the card is edited with the reason - approval resets;
- otherwise (you already applied, or the change is small) only the card's score text is updated and your
  approval stays.
"""

from __future__ import annotations

from jobpilot.cards import build, messages
from jobpilot.core import clock
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.queries import queue as queue_repo
from jobpilot.db.queries import scores as scores_repo
from jobpilot.db.session import transaction
from jobpilot.llm.client import LLMClient
from jobpilot.models.job import PipelineStatus
from jobpilot.reference.normalize import material_jd_change

MATERIAL_POINTS = 5.0  # a score change of this many points (or more) is material


material_change = material_jd_change  # re-exported: the rule lives with the JD fingerprints


async def apply(job_id: int, llm: LLMClient, *, run_id: int | None) -> str:
    """Handle a re-scored job that already has a card. Returns what happened."""
    async with transaction() as conn:
        card = await cards_repo.for_job(conn, job_id)
        history = await scores_repo.history(conn, job_id, limit=2)
    if card is None:
        return "no card"
    new_score = float(history[0]["score"]) if history else None
    old_score = float(history[1]["score"]) if len(history) > 1 else None
    moved = new_score is not None and (old_score is None or abs(new_score - old_score) >= MATERIAL_POINTS)
    reason = (
        f"Full JD arrived; match {old_score:g} -> {new_score:g}."
        if old_score is not None and new_score is not None
        else "Full JD arrived."
    )
    if moved and card["status"] in build.REBUILDABLE:
        await build.build(job_id, llm, run_id=run_id, note=reason + " New resume drafted.")
        outcome = "rebuilt"
    else:
        async with transaction() as conn:
            await messages.edit_card(
                conn, card["id"], reason, f"rescore:{history[0]['id'] if history else 0}"
            )
        outcome = "rescored"
    async with transaction() as conn:
        job = await jobs_repo.get(conn, job_id)
        if job is not None and job["pipeline_status"] != PipelineStatus.CARDED.value:
            await jobs_repo.update(conn, job_id, job["version"], pipeline_status=PipelineStatus.CARDED.value)
        await queue_repo.mark_served(conn, job_id, clock.now())
    return outcome
