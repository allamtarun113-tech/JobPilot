"""Stop conditions, checked after every job.

Card cap, queue empty, budget, API quota, /stop, /pause, kill switch,
error spike.
"""

from __future__ import annotations

from sqlalchemy.engine import RowMapping

from jobpilot.models.run import StopReason
from jobpilot.run.state import RunState

ERROR_SPIKE_THRESHOLD = 3


def evaluate(
    state: RunState, run: RowMapping, *, killswitch: bool, token_budget_left: int, api_quota_left: int | None
) -> StopReason | None:
    """Return why the run should stop now, or None to continue.

    Checked in priority order: kill switch, /stop, /pause, error spike,
    budget, card cap, empty queue.

    Args:
        state: The run state.
        run: The run row (stop and pause flags).
        killswitch: Whether the kill switch is on.
        token_budget_left: Today's remaining token budget.
        api_quota_left: Today's remaining jobs-API calls (reported
            only).
    """
    if killswitch:
        return StopReason.KILLSWITCH
    if run["stop_requested"]:
        return StopReason.STOP_REQUESTED
    if run.get("pause_requested"):
        return StopReason.PAUSED
    if state.get("consecutive_errors", 0) >= ERROR_SPIKE_THRESHOLD:
        return StopReason.ERROR_SPIKE
    if token_budget_left <= 0:
        return StopReason.BUDGET
    # A used-up jobs-API quota does NOT stop the run: scoring and cards don't need the jobs API. The client
    # refuses further calls (QuotaExceeded), so only the jobs-API search and JD lookup are
    # skipped for the rest of the day (`api_quota_left` is kept for callers that want to report it).
    if state.get("cards_made", 0) >= state.get("card_cap", 0):
        return StopReason.CARD_CAP
    if not state.get("pending_job_ids"):
        return StopReason.QUEUE_EMPTY
    return None
