"""Process one job (best stage A first) and card it; then the stop rules decide what's next."""

from __future__ import annotations

import logging

from jobpilot.cards import build as card_build
from jobpilot.cards import rescore
from jobpilot.core import clock
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import queue as queue_repo
from jobpilot.db.session import transaction
from jobpilot.models.run import StopReason
from jobpilot.run import context, pipeline
from jobpilot.run.state import RunState
from jobpilot.run.steps.checks import evaluate
from jobpilot.run.steps.common import mark_step, tracer
from jobpilot.safety import caps

log = logging.getLogger(__name__)


async def process_job(state: RunState) -> RunState:
    """Process the next pending job and card it if it clears the bar.

    The stop rules run first; an error is counted, not raised, so one
    bad job can't end the run.

    Returns:
        State updates for the next step.
    """
    with tracer.start_as_current_span("run.process_job") as span:
        stop = await evaluate(state)
        if stop is not None:
            span.set_attribute("run.stop_reason", stop.value)
            return {"stop_reason": stop, "resume_at": "process_job"}
        pending = list(state["pending_job_ids"])
        job_id = pending.pop(0)
        span.set_attribute("job.id", job_id)
        await mark_step(state, f"process_job:{job_id}")
        ctx = await context.for_run(state["run_id"])
        counts = dict(state.get("counts") or {})
        outcomes = dict(counts.get("pipeline") or {})
        errors = state.get("consecutive_errors", 0)
        cards_made = state.get("cards_made", 0)
        try:
            status = await pipeline.process(job_id, ctx.job_context(state["holder"], state["run_id"]))
            async with transaction() as conn:
                has_card = await cards_repo.for_job(conn, job_id) is not None
            # Already carded: a re-score may rebuild or just update the
            # card.
            if has_card and status in ("queued", "below_bar") and ctx.deps.llm is not None:
                status = await rescore.apply(job_id, ctx.deps.llm, run_id=state["run_id"])
            elif status == "queued" and ctx.deps.llm is not None:
                await card_build.build(job_id, ctx.deps.llm, run_id=state["run_id"])
                async with transaction() as conn:
                    await queue_repo.mark_served(conn, job_id, clock.now())
                status = "carded"
                cards_made += 1
            errors = 0
        except caps.CapReached:
            return {"stop_reason": StopReason.CARD_CAP, "resume_at": "process_job"}
        except card_build.JobBusy:
            status = "busy"  # stays queued; served by a later run
        except Exception as exc:
            log.exception("job %s failed in the pipeline", job_id)
            span.record_exception(exc)
            status, errors = "error", errors + 1
        outcomes[status] = outcomes.get(status, 0) + 1
        counts["pipeline"] = outcomes
        span.set_attribute("job.outcome", status)
        return {
            "pending_job_ids": pending,
            "jobs_processed": state.get("jobs_processed", 0) + 1,
            "counts": counts,
            "cards_made": cards_made,
            "consecutive_errors": errors,
        }
