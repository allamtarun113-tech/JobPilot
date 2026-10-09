"""Summary: one transaction writes the summary to the outbox, finishes the run and releases the lease.

In digest mode the summary is also the daily list of the run's top jobs (each with a 'Show card' button).
A run that cost more than 1.5x the estimate raises an alert.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.bot import messages
from jobpilot.core import clock
from jobpilot.core.errors import LeaseLost
from jobpilot.db.queries import preferences as prefs_repo
from jobpilot.db.queries import runs as runs_repo
from jobpilot.db.session import transaction
from jobpilot.db.tables import cards, jobs, scores
from jobpilot.models.run import RunStatus
from jobpilot.monitoring import alerts
from jobpilot.queue import outbox
from jobpilot.run.state import RunState
from jobpilot.run.steps.common import tracer
from jobpilot.safety import idempotency

DIGEST_TOP_N = 10


async def _digest(conn: AsyncConnection, started_at: Any) -> tuple[str, dict[str, Any] | None]:
    """The run's new cards, best first, as a list with one 'Show card' button each (digest mode only)."""
    prefs = await prefs_repo.get(conn)
    if not (prefs and prefs["data"].get("digest_mode")):
        return "", None
    rows = (
        await conn.execute(
            select(cards.c.callback_token, jobs.c.company, jobs.c.title, scores.c.score)
            .join(jobs, jobs.c.id == cards.c.job_id)
            .join(scores, scores.c.job_id == cards.c.job_id)
            .where(cards.c.created_at >= started_at, cards.c.telegram_outbox_id.is_(None))
            .order_by(scores.c.score.desc())
        )
    ).all()
    best: dict[str, Any] = {}  # a card can have several scores (re-scored); keep its best row
    for row in rows:
        best.setdefault(row[0], row)
    top = list(best.values())[:DIGEST_TOP_N]
    if not top:
        return "", None
    lines = [
        f"{i}. {company or 'Unknown company'} - {title} ({float(score):g}%)"
        for i, (_, company, title, score) in enumerate(top, 1)
    ]
    buttons = [
        [{"text": f"Show card {i}", "callback_data": f"sc:{token}"}]
        for i, (token, *_rest) in enumerate(top, 1)
    ]
    return "\n\nToday's top jobs:\n" + "\n".join(lines), {"inline_keyboard": buttons}


async def summary(state: RunState) -> RunState:
    """Send the run summary, finish the run and release the lease, in one transaction.

    Raises:
        LeaseLost: Another worker took over the run.
    """
    with tracer.start_as_current_span("run.summary"):
        run_id, holder = state["run_id"], state["holder"]
        reason = str(getattr(state.get("stop_reason"), "value", state.get("stop_reason")) or "")
        async with transaction() as conn:
            run = await runs_repo.get(conn, run_id)
            assert run is not None
            tokens = int(run["tokens_in"] + run["tokens_out"])
            digest_text, digest_markup = await _digest(conn, run["started_at"])
            await outbox.telegram_message(
                conn,
                messages.run_summary(
                    run_id,
                    reason,
                    state.get("counts") or {},
                    state.get("cards_made", 0),
                    tokens,
                    float(run["cost_usd"]),
                    state.get("notes") or [],
                )
                + digest_text,
                idempotency_key=idempotency.outbox("run", run_id, "summary"),
                partition_key=outbox.run_partition(run_id),
                reply_markup=digest_markup,
            )
            if problem := alerts.cost_problem(tokens):
                await alerts.send(conn, f"run-cost:{run_id}", problem)
            ok = await runs_repo.update_held(
                conn,
                run_id,
                holder,
                status=RunStatus.FINISHED.value,
                stop_reason=reason,
                stopped_at=clock.now(),
                current_step="done",
                lease_holder=None,
                counts=state.get("counts") or {},
            )
            if not ok:
                raise LeaseLost(f"run {run_id}: lease lost before summary")
        return {}
