"""Fetch: each source reads from its own cursor.

Postings are de-duplicated and stored, and the cursor saved, in one
short transaction per source - only when that source's fetch
succeeded.
"""

from __future__ import annotations

import logging

from opentelemetry import trace

from jobpilot.core import clock
from jobpilot.core.errors import VersionConflict
from jobpilot.db.session import transaction
from jobpilot.monitoring import alerts
from jobpilot.profile import preferences
from jobpilot.run import context
from jobpilot.run.context import RunContext
from jobpilot.run.state import RunState
from jobpilot.run.steps.common import mark_step, tracer
from jobpilot.sources import cursors, dedup, role_expander
from jobpilot.sources.base import SourceUnavailable

log = logging.getLogger(__name__)


async def fetch(state: RunState) -> RunState:
    """Run the fetch step inside its trace span."""
    with tracer.start_as_current_span("run.fetch"):
        return await _fetch(state)


async def refresh_related_titles(ctx: RunContext, run_id: int | None) -> str | None:
    """Weekly (or after your target titles change): ask for related market titles and save them to your
    preferences, so this run already searches them. A failure only costs the refresh, never the run."""
    now = clock.now()
    if ctx.deps.llm is None or not role_expander.needs_refresh(ctx.prefs, now):
        return None
    try:
        suggested = await role_expander.suggest(ctx.deps.llm, ctx.prefs, run_id=run_id)
    except Exception as exc:  # optional step: the saved list is used instead
        log.warning("role expansion failed: %s", exc)
        # Retry on a version conflict: the user may be saving
        # preferences.
        return f"related titles: not refreshed ({type(exc).__name__}); using the saved list"
    for _ in range(3):
        async with transaction() as conn:
            current, version = await preferences.load(conn)
            updated = role_expander.merge(current, suggested, now)
            try:
                await preferences.save(conn, updated, version)
            except VersionConflict:
                continue
        ctx.prefs = updated
        return f"related titles refreshed: {len(updated.related_titles)} titles"
    return "related titles: preferences kept changing; refresh skipped"


async def _fetch(state: RunState) -> RunState:
    """Fetch every source, store new jobs and advance each cursor on success.

    Returns:
        State updates: counts, new job ids, notes and the error count.
    """
    await mark_step(state, "fetch")
    ctx = await context.for_run(state["run_id"])
    counts = dict(state.get("counts") or {})
    notes = list(state.get("notes") or [])
    if note := await refresh_related_titles(ctx, state["run_id"]):
        notes.append(note)
    new_ids: list[int] = list(state.get("new_job_ids") or [])
    errors = 0
    for source in ctx.deps.sources(ctx.prefs, state["run_id"]):
        with tracer.start_as_current_span("run.fetch.source", attributes={"source": source.name}) as span:
            async with transaction() as conn:
                cursor, version = await cursors.load(conn, source.name)
            try:
                result = await source.fetch(cursor)
            except SourceUnavailable as exc:
                counts[source.name] = {"skipped": 1}
                notes.append(f"{source.name}: skipped - {exc}")
                continue
            except Exception as exc:
                errors += 1
                log.exception("source %s failed", source.name)
                span.record_exception(exc)
                span.set_status(trace.Status(trace.StatusCode.ERROR))
                counts[source.name] = {"error": 1}
                notes.append(f"{source.name}: failed ({type(exc).__name__}); its cursor did not move")
                async with transaction() as conn:
                    await alerts.integration_failed(conn, source.name, f"{type(exc).__name__}: {exc}")
                continue
            try:
                async with transaction() as conn:
                    ingested = await dedup.ingest(conn, result.postings, state["run_id"])
                    await cursors.save(conn, source.name, result.cursor, version)
                    await alerts.integration_ok(conn, source.name)
            except VersionConflict:
                notes.append(
                    f"{source.name}: cursor changed during the fetch; results not saved, retry next run"
                )
                continue
            fresh = [i.job_id for i in ingested if i.new_job]
            new_ids += [j for j in fresh if j not in new_ids]
            counts[source.name] = {**result.stats, "new": len(fresh), "dupes": len(ingested) - len(fresh)}
            notes += [f"{source.name}: {n}" for n in result.notes]
            span.set_attributes({f"fetch.{k}": v for k, v in counts[source.name].items()})
    await mark_step(state, "fetch", counts=counts)
    return {
        "counts": counts,
        "new_job_ids": new_ids,
        "notes": notes,
        "consecutive_errors": state.get("consecutive_errors", 0) + errors,
    }
