"""Small timed jobs only. It never starts a run.

- every heartbeat: the run lease and claimed work items;
- every minute: health alerts (stuck run, outbox lag, dead rows, 80% budget / quota), the 24-hour email
  re-confirm sweep, queue expiry;
- daily: the reminder digest (cards with no action for 48 h, snoozes that are due) and redaction of event
  payloads older than 180 days.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from jobpilot.cards import reminders
from jobpilot.core import clock
from jobpilot.core.config import get_config
from jobpilot.db.queries import work_items
from jobpilot.db.session import transaction
from jobpilot.monitoring import health
from jobpilot.queue import reconfirm
from jobpilot.run import lease
from jobpilot.sources import queue_rank

log = logging.getLogger(__name__)
HOUSEKEEPING_SECONDS = 60
# When each timed job last ran (in memory; resets on restart).
_last: dict[str, datetime] = {}


def _due(name: str, seconds: float) -> bool:
    """Return True (and record the time) if ``name`` hasn't run for ``seconds``."""
    now = clock.now()
    last = _last.get(name)
    if last is not None and (now - last).total_seconds() < seconds:
        return False
    _last[name] = now
    return True


async def tick(holder: str) -> None:
    """Refresh the run lease and the leases of claimed work items."""
    cfg = get_config()
    await lease.heartbeat(holder)
    async with transaction() as conn:
        await work_items.extend_held(conn, holder, cfg.lease.work_item_ttl_seconds)


async def housekeeping() -> None:
    """Run the per-minute jobs, each in its own short transaction."""
    async with transaction() as conn:
        await health.check(conn)
    async with transaction() as conn:
        await reconfirm.sweep(conn)
    async with transaction() as conn:
        await queue_rank.expire(conn, clock.now())
    async with transaction() as conn:
        await reminders.send_daily_reminder(conn)


async def daily() -> None:
    """Run the daily job: redact old event payloads."""
    async with transaction() as conn:
        await health.redact_old_events(conn)


async def run(holder: str) -> None:
    """Run the timed jobs forever, every heartbeat interval.

    Args:
        holder: This worker's lease holder id.
    """
    interval = get_config().lease.heartbeat_seconds
    while True:
        for name, job, period in (
            ("tick", lambda: tick(holder), 0),
            ("housekeeping", housekeeping, HOUSEKEEPING_SECONDS),
            ("daily", daily, 24 * 3600),
        ):
            if period and not _due(name, period):
                continue
            try:
                await job()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("scheduler %s failed", name)
        await asyncio.sleep(interval)
