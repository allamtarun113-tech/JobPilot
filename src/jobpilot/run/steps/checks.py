"""Shared stop-rule evaluation for the run nodes: kill switch, /stop, /pause, errors, budget, API quota, cap.

An error spike or a budget breach also engages the kill switch, with an alert.
A budget kill switch is released by preflight on a later day once the budget is available again.
"""

from __future__ import annotations

from jobpilot.db.queries import runs as runs_repo
from jobpilot.db.session import transaction
from jobpilot.models.run import StopReason
from jobpilot.monitoring import alerts
from jobpilot.run import stop_rules
from jobpilot.run.state import RunState
from jobpilot.safety import caps, killswitch

AUTO_HALT = {StopReason.ERROR_SPIKE: "error spike", StopReason.BUDGET: "budget"}


async def evaluate(state: RunState) -> StopReason | None:
    """Check the stop rules for the run right now.

    An error spike or a budget breach also engages the kill switch and
    sends an alert.

    Args:
        state: The run state.

    Returns:
        The reason to stop, or None to go on.
    """
    async with transaction() as conn:
        run = await runs_repo.get(conn, state["run_id"])
        killed = await killswitch.is_engaged(conn)
        budget_left = await caps.token_budget_left(conn)
        quota_left = await caps.api_quota_left(conn)
    assert run is not None
    stop = stop_rules.evaluate(
        state, run, killswitch=killed, token_budget_left=budget_left, api_quota_left=quota_left
    )
    if stop in AUTO_HALT:
        async with transaction() as conn:
            await killswitch.engage(conn, AUTO_HALT[stop])
            await alerts.send(
                conn,
                f"killswitch:{AUTO_HALT[stop]}",
                f"Kill switch engaged automatically ({AUTO_HALT[stop]}) in run #{state['run_id']}. "
                "Release it with /killswitch off (or on the dashboard Settings page) when it is safe.",
            )
    return stop
