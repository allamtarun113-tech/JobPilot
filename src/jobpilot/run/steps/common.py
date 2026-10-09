"""Helpers shared by run nodes: step bookkeeping under the lease, and node spans."""

from __future__ import annotations

from typing import Any

from opentelemetry import trace

from jobpilot.core.errors import LeaseLost
from jobpilot.db.queries import runs as runs_repo
from jobpilot.db.session import transaction
from jobpilot.run.state import RunState

tracer = trace.get_tracer("jobpilot.run")


async def mark_step(state: RunState, step: str, **values: Any) -> None:
    """Record the current step (short write). Raises LeaseLost if another holder took the run over."""
    async with transaction() as conn:
        ok = await runs_repo.update_held(conn, state["run_id"], state["holder"], current_step=step, **values)
    if not ok:
        raise LeaseLost(f"run {state['run_id']}: lease no longer held by {state['holder']}")
