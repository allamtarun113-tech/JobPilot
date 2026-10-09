"""Serve the ranked jobs queue before fetching anything new.

Best first, by score with age decay; entries older than 10 days
expire. The stop rules run between queued jobs, exactly as in
process_job.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.cards import build as card_build
from jobpilot.core import clock
from jobpilot.core.config import get_config
from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.queries import queue as queue_repo
from jobpilot.db.queries import scores as scores_repo
from jobpilot.db.session import transaction
from jobpilot.models.job import PipelineStatus
from jobpilot.models.run import StopReason
from jobpilot.run import context
from jobpilot.run.state import RunState
from jobpilot.run.steps.checks import evaluate
from jobpilot.run.steps.common import mark_step, tracer
from jobpilot.safety import caps
from jobpilot.sources import queue_rank

log = logging.getLogger(__name__)
# Consecutive card-build failures that trigger the error-spike check.
ERROR_SPIKE = 3


async def _promote_now_above_bar(conn: AsyncConnection, threshold: int, now: datetime) -> int:
    """Jobs scored below your bar that clear it now (you lowered the threshold) join the queue. The score is
    deterministic and already stored, so no model call is needed."""
    since = now - timedelta(days=get_config().queue.expiry_days)
    below = [
        j
        for j in await jobs_repo.by_status(conn, (PipelineStatus.BELOW_BAR.value,))
        if j["first_seen_at"] >= since
    ]
    latest = await scores_repo.latest_for_many(conn, [j["id"] for j in below])
    promoted = 0
    for job in below:
        score = latest.get(job["id"])
        if score is not None and float(score["score"]) >= threshold:
            await queue_rank.enqueue(conn, job["id"], float(score["score"]), now)
            await jobs_repo.update(
                conn,
                job["id"],
                job["version"],
                pipeline_status=PipelineStatus.QUEUED.value,
                filter_reason=None,
            )
            promoted += 1
    return promoted


async def serve_queue(state: RunState) -> RunState:
    """Card the best queued jobs until a stop rule says stop.

    Returns:
        Queue counts, cards made, errors and any stop reason.
    """
    with tracer.start_as_current_span("run.serve_queue") as span:
        await mark_step(state, "serve_queue")
        now = clock.now()
        ctx = await context.for_run(state["run_id"])
        async with transaction() as conn:
            promoted = await _promote_now_above_bar(conn, ctx.prefs.threshold, now)
            expired = await queue_rank.expire(conn, now)
            for job_id in expired:
                row = await jobs_repo.get(conn, job_id)
                if row is not None:
                    await jobs_repo.update(
                        conn, job_id, row["version"], pipeline_status=PipelineStatus.EXPIRED.value
                    )
            waiting = await queue_rank.ranked(conn, now, ctx.prefs)
        made = state.get("cards_made", 0)
        errors = state.get("consecutive_errors", 0)
        stop: StopReason | None = None
        if ctx.deps.llm is not None:
            for job_id, _score in waiting:
                check_state: RunState = {
                    **state,
                    "cards_made": made,
                    "consecutive_errors": errors,
                    "pending_job_ids": [job_id],
                }
                stop = await evaluate(check_state)
                if stop is not None:
                    break
                try:
                    await card_build.build(job_id, ctx.deps.llm, run_id=state["run_id"])
                    async with transaction() as conn:
                        await queue_repo.mark_served(conn, job_id, clock.now())
                    made += 1
                    errors = 0
                except caps.CapReached:
                    stop = StopReason.CARD_CAP
                    break
                except card_build.JobBusy:
                    continue
                except Exception:
                    log.exception("failed to build queued card for job %s", job_id)
                    errors += 1
                    if errors >= ERROR_SPIKE:
                        stop = await evaluate({**state, "cards_made": made, "consecutive_errors": errors})
                        break
        span.set_attributes({"queue.expired": len(expired), "queue.waiting": len(waiting)})
        counts = dict(state.get("counts") or {})
        counts["queue"] = {"waiting": len(waiting), "expired": len(expired), "promoted": promoted}
        return {
            "counts": counts,
            "cards_made": made,
            "consecutive_errors": errors,
            "stop_reason": stop,
            "resume_at": "serve_queue" if stop == StopReason.PAUSED else None,
        }
