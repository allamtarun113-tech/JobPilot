"""Preflight: kill switch, token budget, confirmed profile and jobs-API quota."""

from __future__ import annotations

from jobpilot.core.config import get_config
from jobpilot.db.queries import flags
from jobpilot.db.session import transaction
from jobpilot.models.run import StopReason
from jobpilot.monitoring import alerts
from jobpilot.run import context
from jobpilot.run.state import RunState
from jobpilot.run.steps.common import mark_step, tracer
from jobpilot.safety import caps, killswitch


async def jobs_api_calls_today() -> int:
    """Return the paid jobs-API calls made today."""
    async with transaction() as conn:
        return await caps.api_calls_today(conn)


async def preflight(state: RunState) -> RunState:
    """Check the kill switch, budget, profile and quota before the run starts.

    Returns:
        The checks, a stop reason if one blocks the run, and notes.
    """
    with tracer.start_as_current_span("run.preflight") as span:
        await mark_step(state, "preflight")
        async with transaction() as conn:
            switch = await flags.get(conn, flags.KILLSWITCH) or {}
            budget_left = await caps.token_budget_left(conn)
            if switch.get("on") and switch.get("reason") == "budget" and budget_left > 0:
                await killswitch.release(conn)  # a new day's budget: the automatic budget halt lifts itself
                switch = {}
        killed = bool(switch.get("on"))
        ctx = await context.for_run(state["run_id"])
        calls = await jobs_api_calls_today()
        cap = get_config().caps.jobs_api_calls_per_day
        checks = {
            "killswitch": f"engaged ({switch.get('reason')})" if killed else "ok",
            "token_budget": "ok" if budget_left > 0 else "exhausted",
            "profile": f"{ctx.view.confirmed_count} confirmed items"
            if ctx.view.confirmed_count
            else "not confirmed",
            "llm": "ok" if ctx.deps.llm else "no OPENAI_API_KEY (jobs wait for extraction)",
            "jobs_api": f"{calls}/{cap} calls today" if ctx.deps.jobs_api else "no jobs API key",
        }
        span.set_attributes({f"preflight.{k}": v for k, v in checks.items()})
        stop: StopReason | None = None
        notes: list[str] = []
        if killed:
            stop = StopReason.KILLSWITCH
            notes.append("The kill switch is on. Release it with /killswitch off when it is safe.")
        elif budget_left <= 0:
            stop = StopReason.BUDGET
        elif ctx.view.confirmed_count == 0:
            stop = StopReason.PREFLIGHT_FAILED
            notes.append(
                "Confirm your profile on the dashboard (/profile) first - scoring uses only confirmed facts."
            )
        if ctx.deps.jobs_api and calls >= cap:  # not a stop: the run continues without the jobs API
            notes.append(f"Jobs API: today's {cap} calls are used; it is skipped until midnight.")
        if ctx.deps.jobs_api and (problem := alerts.quota_problem(calls, cap)) and not stop:
            notes.append(problem)
        return {"preflight": checks, "stop_reason": stop, "notes": notes}
