"""System-health checks run by the worker's scheduler, each turned into an alert through the outbox.

- a running run with no step change for `alerts.stuck_minutes` (10 min);
- the outbox not draining (a ready row waiting that long) and dead rows (a paused partition);
- 80% of today's token budget, 80% of today's jobs-API calls;
- PII hygiene: event payloads older than `privacy.event_payload_retention_days` (180) are redacted.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core import clock
from jobpilot.core.config import get_config
from jobpilot.db.queries import flags
from jobpilot.db.queries import outbox as outbox_repo
from jobpilot.db.queries import runs as runs_repo
from jobpilot.db.tables import events
from jobpilot.monitoring import alerts
from jobpilot.monitoring.redaction import redact_value
from jobpilot.safety import caps


async def check(conn: AsyncConnection) -> list[str]:
    """Run every check; returns the alert keys raised (alerts are deduplicated per key per day)."""
    cfg = get_config()
    raised: list[str] = []
    stuck = timedelta(minutes=cfg.alerts.stuck_minutes)
    run = await runs_repo.get_active(conn)
    if run is not None and run["updated_at"] < clock.now() - stuck:
        await alerts.send(
            conn,
            f"stuck-run:{run['id']}",
            f"Run #{run['id']} has made no progress for {cfg.alerts.stuck_minutes} minutes.",
        )
        raised.append("stuck-run")
    age = await outbox_repo.oldest_pending_age_seconds(conn)
    if age is not None and age > stuck.total_seconds():
        await alerts.send(
            conn, "outbox-lag", f"Messages have been waiting to send for {age / 60:.0f} minutes."
        )
        raised.append("outbox-lag")
    for head in await outbox_repo.dead_heads(conn):
        if head["partition_key"].startswith("alert:"):
            continue  # never alert about an alert that itself failed
        await alerts.send(
            conn,
            f"dead:{head['id']}",
            f"A {head['kind']} message is stuck ({head['partition_key']}): {str(head['last_error'])[:160]}. "
            "See docs/runbook.md - outbox recovery.",
        )
        raised.append("dead")
    used, budget = await caps.tokens_used_today(conn), cfg.budgets.tokens_per_day
    if problem := alerts.budget_problem(used, budget):
        await alerts.send(conn, "budget", problem)
        raised.append("budget")
    calls = await caps.api_calls_today(conn)
    if problem := alerts.quota_problem(calls, cfg.caps.jobs_api_calls_per_day):
        await alerts.send(conn, "quota", problem)
        raised.append("quota")
    return raised


async def redact_old_events(conn: AsyncConnection, limit: int = 500) -> int:
    """Mask emails and phone numbers in event payloads past the retention window (once per row)."""
    cutoff = clock.now() - timedelta(days=get_config().privacy.event_payload_retention_days)
    rows = await conn.execute(
        select(events.c.id, events.c.payload)
        .where(events.c.created_at < cutoff, events.c.payload["redacted"].as_boolean().is_not(True))
        .limit(limit)
    )
    count = 0
    for event_id, payload in rows.all():
        clean: dict[str, Any] = {**redact_value(payload), "redacted": True}
        await conn.execute(update(events).where(events.c.id == event_id).values(payload=clean))
        count += 1
    return count


async def counters(conn: AsyncConnection) -> dict[str, Any]:
    """For the Settings page: outbox lag/dead, lease takeovers, consecutive integration failures."""
    return {
        **(await outbox_repo.lag(conn)),
        **dict(await flags.get(conn, "health_counters") or {}),
        "integration_failures": dict(await flags.get(conn, alerts.INTEGRATION_FAILURES) or {}),
    }
