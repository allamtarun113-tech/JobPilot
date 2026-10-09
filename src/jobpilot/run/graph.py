"""The batch run: preflight -> serve_queue -> fetch -> process_job (loop) -> drain -> summary.

A run always finishes. LangGraph checkpoints (Postgres) hold only its in-flight steps, so a run whose
worker crashed is resumed by the next /start after its lease goes stale. Nothing here waits for a human:
cards are database state machines moved by events.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from opentelemetry import trace

from jobpilot.bot import messages
from jobpilot.core import clock
from jobpilot.core.errors import LeaseLost
from jobpilot.db.queries import runs as runs_repo
from jobpilot.db.session import read, transaction
from jobpilot.models.run import RunStatus, StopReason
from jobpilot.monitoring.tracing import current_trace_id
from jobpilot.queue import outbox
from jobpilot.run import context, lease
from jobpilot.run.state import RunState
from jobpilot.run.steps.drain import drain
from jobpilot.run.steps.fetch import fetch
from jobpilot.run.steps.preflight import preflight
from jobpilot.run.steps.process_job import process_job
from jobpilot.run.steps.screen import screen
from jobpilot.run.steps.serve_queue import serve_queue
from jobpilot.run.steps.summary import summary
from jobpilot.safety import caps, idempotency

log = logging.getLogger(__name__)
tracer = trace.get_tracer("jobpilot.run")


def _entry(state: RunState) -> str:
    """A run continued after /pause re-enters where it paused; otherwise it starts at preflight."""
    return state.get("resume_at") or "preflight"


def _stopped(state: RunState) -> str | None:
    """Return the node to go to when a stop reason is set (pause or drain)."""
    reason = state.get("stop_reason")
    if reason == StopReason.PAUSED.value or reason == StopReason.PAUSED:
        return "pause"
    return "drain" if reason else None


def _after_preflight(state: RunState) -> str:
    """Route after preflight: stop, or serve the queue."""
    return _stopped(state) or "serve_queue"


def _after_process(state: RunState) -> str:
    """Route after a job: stop, or process the next one."""
    return _stopped(state) or "process_job"


def _after_serve_queue(state: RunState) -> str:
    """Route after the queue: stop, drain at the card cap, or fetch."""
    if stopped := _stopped(state):
        return stopped
    return "drain" if state.get("cards_made", 0) >= state.get("card_cap", 0) else "fetch"


async def pause(state: RunState) -> RunState:
    """Hold after the current job: release the lease, keep the place (the checkpoint), tell the user."""
    run_id, holder = state["run_id"], state["holder"]
    async with transaction() as conn:
        ok = await runs_repo.mark_paused(conn, run_id, holder, dict(state.get("counts") or {}))
        if not ok:
            raise LeaseLost(f"run {run_id}: lease lost before pausing")
        await outbox.telegram_message(
            conn,
            messages.run_paused(run_id),
            partition_key=outbox.run_partition(run_id),
            idempotency_key=idempotency.outbox("run", run_id, "paused", state.get("jobs_processed", 0)),
        )
    return {}


def build_graph(checkpointer: BaseCheckpointSaver | None) -> Any:
    """Build the run graph with its fixed step order.

    Args:
        checkpointer: LangGraph's Postgres saver, or None in tests.

    Returns:
        The compiled graph.
    """
    g = StateGraph(RunState)
    g.add_node("preflight", preflight)
    g.add_node("serve_queue", serve_queue)
    g.add_node("fetch", fetch)
    g.add_node("screen", screen)
    g.add_node("process_job", process_job)
    g.add_node("drain", drain)
    g.add_node("summary", summary)
    g.add_node("pause", pause)
    g.add_conditional_edges(START, _entry, ["preflight", "serve_queue", "process_job"])
    g.add_conditional_edges("preflight", _after_preflight, ["serve_queue", "drain", "pause"])
    g.add_conditional_edges("serve_queue", _after_serve_queue, ["fetch", "drain", "pause"])
    g.add_edge("fetch", "screen")
    g.add_edge("screen", "process_job")
    g.add_conditional_edges("process_job", _after_process, ["process_job", "drain", "pause"])
    g.add_edge("drain", "summary")
    g.add_edge("summary", END)
    g.add_edge("pause", END)
    return g.compile(checkpointer=checkpointer)


async def execute_run_request(
    payload: dict[str, Any], *, holder: str, checkpointer: BaseCheckpointSaver | None
) -> None:
    """Handle a 'run_request' work item: take the lease, then run (or resume) the batch graph."""
    card_cap = int(payload.get("card_cap") or 0)
    if card_cap <= 0:  # plain /start: your cards-per-day preference (within the config ceiling)
        async with read() as conn:
            card_cap = await caps.card_limit(conn)
    request_key = str(payload.get("request_key", "manual"))
    got = await lease.acquire(holder, card_cap, request_key, resume_only=bool(payload.get("resume")))
    if isinstance(got, lease.NothingPaused):
        async with transaction() as conn:
            await outbox.telegram_message(
                conn,
                messages.nothing_paused(),
                idempotency_key=idempotency.outbox("run-request", request_key, "nothing-paused"),
            )
        return
    if isinstance(got, lease.Held):
        async with transaction() as conn:
            await outbox.telegram_message(
                conn,
                messages.run_already_active(got.run["id"], got.run["current_step"]),
                idempotency_key=idempotency.outbox("run-request", request_key, "held"),
            )
        return

    run_id = got.run_id
    graph = build_graph(checkpointer)
    # One LangGraph thread per run, so a crashed run resumes its own
    # checkpoint.
    config = {"configurable": {"thread_id": f"run-{run_id}"}, "recursion_limit": 10_000}
    with tracer.start_as_current_span("run", attributes={"run.id": run_id, "run.resumed": got.resumed}):
        async with transaction() as conn:
            await runs_repo.update_held(conn, run_id, holder, trace_id=current_trace_id())
        try:
            resume = False
            snapshot = None
            # A stale run taken over: continue from the checkpoint if
            # steps
            # are left.
            if got.resumed and checkpointer is not None:
                snapshot = await graph.aget_state(config)
                resume = bool(snapshot.next)
            if got.from_pause and snapshot is not None and snapshot.values:
                # Continue the paused run: same id, same counts and pending jobs, from where it held.
                values = dict(snapshot.values)
                continued: RunState = {
                    **values,  # type: ignore[typeddict-item]
                    "holder": holder,
                    "stop_reason": None,
                    "consecutive_errors": 0,
                    "resume_at": values.get("resume_at") or "process_job",
                }
                await graph.ainvoke(continued, config)
            elif resume:
                await graph.aupdate_state(config, {"holder": holder})
                await graph.ainvoke(None, config)
            else:
                initial: RunState = {
                    "run_id": run_id,
                    "holder": holder,
                    "card_cap": got.card_cap,
                    "pending_job_ids": [],
                    "new_job_ids": [],
                    "notes": [],
                    "cards_made": 0,
                    "jobs_processed": 0,
                    "consecutive_errors": 0,
                    "counts": {},
                    "stop_reason": None,
                    "resume_at": None,
                }
                await graph.ainvoke(initial, config)
        except LeaseLost:
            log.warning("run %s: lease lost; another worker continues it", run_id)
        except Exception as exc:
            log.exception("run %s failed", run_id)
            await _fail_run(run_id, holder, exc)
        finally:
            context.forget(run_id)


async def _fail_run(run_id: int, holder: str, exc: Exception) -> None:
    """Mark a run failed, release its lease and tell the user (if still held)."""
    async with transaction() as conn:
        ok = await runs_repo.update_held(
            conn,
            run_id,
            holder,
            status=RunStatus.FAILED.value,
            stop_reason=StopReason.ERROR.value,
            stopped_at=clock.now(),
            lease_holder=None,
            current_step="failed",
        )
        if ok:
            await outbox.telegram_message(
                conn,
                messages.run_failed(run_id, type(exc).__name__),
                partition_key=outbox.run_partition(run_id),
                idempotency_key=idempotency.outbox("run", run_id, "failed"),
            )
