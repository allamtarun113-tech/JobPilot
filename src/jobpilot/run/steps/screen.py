"""Screen the new jobs on their snippets.

Hard filters (with logged reasons), then stage A similarity. Every
survivor goes on to the full-JD lookup and scoring (decision 27),
ordered by location (your cities, remote, elsewhere) and then stage A.
Jobs waiting for extraction from earlier runs rejoin here.
"""

from __future__ import annotations

from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.session import transaction
from jobpilot.matching import embed_score, hard_filters
from jobpilot.models.job import PipelineStatus
from jobpilot.run import context
from jobpilot.run.state import RunState
from jobpilot.run.steps.common import mark_step, tracer
from jobpilot.sources import queue_rank


async def screen(state: RunState) -> RunState:
    """Filter new jobs, score them by stage A and order them for processing.

    Returns:
        The pending job ids (best first) and filter counts.
    """
    with tracer.start_as_current_span("run.screen") as span:
        await mark_step(state, "screen")
        ctx = await context.for_run(state["run_id"])
        async with transaction() as conn:
            discovered = [
                dict(j)
                for j in await jobs_repo.by_ids(conn, state.get("new_job_ids") or [])
                if j["pipeline_status"] == PipelineStatus.NEW.value
            ]
            # A previous run can leave a NEW job after a transient pipeline error.
            # Dedup then reports it as existing; it must still be screened again.
            waiting = await jobs_repo.by_status(conn, (PipelineStatus.NEW.value,))
            new_jobs = list({j["id"]: dict(j) for j in [*discovered, *waiting]}.values())
        survivors, filtered = [], 0
        for job in new_jobs:
            reason = hard_filters.check(job, ctx.prefs)
            if reason:
                filtered += 1
                async with transaction() as conn:
                    await jobs_repo.update(
                        conn,
                        job["id"],
                        job["version"],
                        pipeline_status=PipelineStatus.FILTERED.value,
                        filter_reason=reason,
                    )
            else:
                survivors.append(job)
        sims = await embed_score.stage_a(
            survivors, await ctx.profile_vector(), ctx.deps.embedder, ctx.deps.vectors
        )
        kept = sorted(survivors, key=lambda j: (queue_rank.location_rank(j, ctx.prefs), -sims[j["id"]]))
        async with transaction() as conn:
            for job in survivors:
                row = await jobs_repo.get(conn, job["id"])
                assert row is not None
                await jobs_repo.update(conn, job["id"], row["version"], stage_a=sims[job["id"]])
            retry = []
            if ctx.deps.llm is not None:  # jobs that waited for the API key get processed now
                retry = [
                    j["id"]
                    for j in await jobs_repo.by_status(conn, (PipelineStatus.PENDING_EXTRACTION.value,))
                ]
        pending = [j["id"] for j in kept] + [i for i in retry if i not in {j["id"] for j in kept}]
        span.set_attributes({"screen.filtered": filtered, "screen.kept": len(kept)})
        counts = dict(state.get("counts") or {})
        counts["pipeline"] = {"filtered": filtered}
        return {"pending_job_ids": pending, "counts": counts}
