"""The digest (/digest) and the reminder for cards with no action.

- jobs below your bar,
- jobs waiting in the queue for a card,
- cards with no action for `reminders.no_action_after_hours` (48 h), and snoozed cards that are due again.

The scheduler sends the reminder digest at most once a day (after `reminders.digest_hour` local time) and only
when some card actually needs you. It never starts a run.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.cards import states
from jobpilot.core import clock
from jobpilot.core.config import get_config
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.tables import cards, jobs
from jobpilot.models.job import PipelineStatus
from jobpilot.queue import outbox
from jobpilot.safety import caps, idempotency
from jobpilot.sources import queue_rank

LIST_LIMIT = 5


async def cards_needing_action(conn: AsyncConnection) -> list[dict[str, Any]]:
    """Cards waiting for you for 48 h+, or snoozed cards whose time has come."""
    now = clock.now()
    stale = now - timedelta(hours=get_config().reminders["no_action_after_hours"])
    rows = await conn.execute(
        select(cards, jobs.c.company, jobs.c.title)
        .join(jobs, jobs.c.id == cards.c.job_id)
        .where(
            cards.c.status.in_(states.NO_ACTION_STATES),
            or_(
                and_(cards.c.snoozed_until.is_(None), cards.c.updated_at <= stale),
                cards.c.snoozed_until <= now,
            ),
        )
        .order_by(cards.c.updated_at)
    )
    return [dict(r) for r in rows.mappings()]


async def digest(conn: AsyncConnection) -> tuple[str, dict[str, Any] | None]:
    """Build the digest message and buttons for cards that need you.

    Returns:
        (the message text, an inline keyboard or None).
    """
    below = await jobs_repo.latest(conn, status=PipelineStatus.BELOW_BAR.value, limit=LIST_LIMIT)
    waiting = await queue_rank.ranked(conn, clock.now())
    queued = await jobs_repo.by_ids(conn, [job_id for job_id, _ in waiting[:LIST_LIMIT]])
    action = await cards_needing_action(conn)
    lines = ["Your digest"]

    def section(title: str, rows: list[Any], extra: str = "") -> None:
        """Append one titled list (or "none") to the digest lines."""
        lines.append(f"\n{title}{extra}:" if rows else f"\n{title}: none")
        for r in rows[:LIST_LIMIT]:
            lines.append(f"- {r['company'] or 'Unknown company'} - {r['title']}")

    section("Cards waiting for you", action)
    section("Queued for a card", queued, f" ({len(waiting)} total)")
    section("Below your bar", below)
    buttons = [
        [
            {
                "text": f"{c['company'] or 'Job'} - {c['title']}"[:60],
                "callback_data": f"sc:{c['callback_token']}",
            }
        ]
        for c in action[:LIST_LIMIT]
    ]
    return "\n".join(lines), ({"inline_keyboard": buttons} if buttons else None)


async def send_daily_reminder(conn: AsyncConnection) -> bool:
    """Called by the scheduler. Sends the digest once a day when some card needs action. True if sent."""
    now_local = clock.now().astimezone(get_config().tz)
    if now_local.hour < get_config().reminders.get("digest_hour", 9):
        return False
    action = await cards_needing_action(conn)
    day_start = caps.start_of_day()
    # Remind about each card at most once a day.
    due = [c for c in action if c["reminded_at"] is None or c["reminded_at"] < day_start]
    if not due:
        return False
    text, markup = await digest(conn)
    await outbox.telegram_message(
        conn,
        "Reminder - these cards are waiting for you.\n\n" + text,
        idempotency_key=idempotency.outbox("reminder", now_local.date().isoformat()),
        reply_markup=markup,
    )
    for card in due:  # version-checked like every other card write
        await cards_repo.update(
            conn, card["id"], card["version"], reminded_at=clock.now(), snoozed_until=None
        )
    return True
