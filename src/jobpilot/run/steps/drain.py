"""Drain: the job in progress is finished; save cursors, leave the rest queued."""

from __future__ import annotations

from jobpilot.models.run import RunStatus
from jobpilot.run.state import RunState
from jobpilot.run.steps.common import mark_step, tracer


async def drain(state: RunState) -> RunState:
    """Mark the run draining; the summary step follows."""
    with tracer.start_as_current_span("run.drain"):
        await mark_step(state, "drain", status=RunStatus.DRAINING.value)
        # Each source's cursor was saved with the jobs it produced (sources/cursors.py).
        return {}
