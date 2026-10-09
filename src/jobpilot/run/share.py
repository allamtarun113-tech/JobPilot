"""A job the user shared to the bot.

Recall bookkeeping, then the same pipeline as any other job (the user
chose it, so it skips the snippet filters and stage A), and - if it
clears the bar - a card like any other, right away (within the daily
card cap).
"""

from __future__ import annotations

import logging

from jobpilot.bot import messages
from jobpilot.cards import build as card_build
from jobpilot.core import clock
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import events as events_repo
from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.queries import queue as queue_repo
from jobpilot.db.queries import scores as scores_repo
from jobpilot.db.queries import shared_jobs
from jobpilot.db.session import transaction
from jobpilot.models.job import PipelineStatus
from jobpilot.queue import outbox
from jobpilot.run import context, pipeline
from jobpilot.safety import caps, idempotency
from jobpilot.sources import dedup
from jobpilot.sources.shared_links import posting_from_share

log = logging.getLogger(__name__)
DONE = {PipelineStatus.QUEUED.value, PipelineStatus.BELOW_BAR.value, PipelineStatus.CARDED.value}


async def _reply(event_id: int, text: str) -> None:
    """Send the one reply for this shared-job event."""
    async with transaction() as conn:
        await outbox.telegram_message(conn, text, idempotency_key=idempotency.outbox("share", event_id))


async def handle_share(event_id: int) -> None:
    """Handle a shared link: record recall, score the job and maybe card it.

    Args:
        event_id: The stored Telegram message event.
    """
    async with transaction() as conn:
        event = await events_repo.get(conn, event_id)
    assert event is not None
    msg = event["payload"].get("message") or {}
    posting = posting_from_share(msg.get("text") or msg.get("caption") or "", event_id)
    if posting is None:
        await _reply(event_id, messages.share_no_link())
        return
    async with transaction() as conn:
        existing, _layer = await dedup.find_existing(conn, dedup.normalize_posting(posting))
        hit = False
        if existing is not None:
            # A hit: some source other than sharing already had the job.
            hit = any(s["source"] != "shared" for s in await jobs_repo.sources_for(conn, existing["id"]))
        (ingested,) = await dedup.ingest(conn, [posting], None)
        await shared_jobs.add(conn, job_id=ingested.job_id, event_id=event_id, url=posting.url, hit=hit)
        job = await jobs_repo.get(conn, ingested.job_id)
    assert job is not None
    status = job["pipeline_status"]
    ctx = await context.build()
    if status not in DONE:
        if ctx.view.confirmed_count == 0:
            await _reply(
                event_id, messages.share_result(hit, job["title"], job["company"], "unconfirmed", None)
            )
            return
        try:
            status = await pipeline.process(job["id"], ctx.job_context(f"share-{event_id}", None))
        except Exception:
            log.exception("shared job %s failed in the pipeline", job["id"])
            status = "error"
    if status == "queued" and ctx.deps.llm is not None:
        async with transaction() as conn:
            has_card = await cards_repo.for_job(conn, job["id"]) is not None
        if not has_card:
            try:
                await card_build.build(job["id"], ctx.deps.llm, run_id=None)
                async with transaction() as conn:
                    await queue_repo.mark_served(conn, job["id"], clock.now())
                status = "carded_now"
            except caps.CapReached:
                status = "queued_cap"
            except card_build.JobBusy:
                status = "queued"
            except Exception:
                log.exception("shared job %s: card build failed", job["id"])
                status = "card_error"
    async with transaction() as conn:
        score = await scores_repo.latest_for(conn, job["id"])
    await _reply(
        event_id,
        messages.share_result(
            hit, job["title"], job["company"], status, float(score["score"]) if score else None
        ),
    )
