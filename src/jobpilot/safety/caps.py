"""Budgets and caps: tokens / day, Job Chat tokens / day, cards / day, emails / day, jobs-API calls / day.

Days start at local midnight (config `timezone`). Capacity checks run inside short transactions under a
transaction-scoped advisory lock - never across an LLM, storage or MCP call.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core import clock
from jobpilot.core.config import get_config
from jobpilot.db.queries import emails
from jobpilot.db.queries import llm_usage as llm_usage_repo
from jobpilot.db.queries import preferences as prefs_repo
from jobpilot.db.tables import api_usage, card_reservations, cards, jobs, llm_usage, outbox
from jobpilot.models.preferences import Preferences

log = logging.getLogger(__name__)
# Advisory lock id that serialises every capacity check.
CAPACITY_LOCK = 7264010


class CapReached(ValueError):
    """A daily cap or an existing reservation prevents starting another effect.

    `retry_at` says when the same effect may be tried again (None: not by waiting)."""

    def __init__(self, message: str, retry_at: datetime | None = None) -> None:
        """Create the error.

        Args:
            message: Which cap was hit.
            retry_at: When waiting would help, or None.
        """
        super().__init__(message)
        self.retry_at = retry_at


def start_of_day(now: datetime | None = None) -> datetime:
    """Local midnight (config timezone) of the current day, as an aware datetime."""
    tz = get_config().tz
    local = (now or clock.now()).astimezone(tz)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def next_day(now: datetime | None = None) -> datetime:
    """Return the next local midnight, when daily caps reset."""
    return start_of_day(now) + timedelta(days=1)


async def _prefs(conn: AsyncConnection) -> Preferences:
    """Load the user's preferences, or defaults from config."""
    cfg = get_config()
    row = await prefs_repo.get(conn)
    if row is None:
        return Preferences(
            threshold=cfg.match_threshold,
            cards_per_day=cfg.caps.cards_per_day,
        )
    return Preferences.model_validate(row["data"])


# ---- tokens ------------------------------------------------------------------------------------------


async def tokens_used_today(conn: AsyncConnection) -> int:
    """Return the LLM tokens used since local midnight."""
    return await llm_usage_repo.tokens_since(conn, start_of_day())


UNLIMITED = 10**12  # budget left when there is no daily token limit


async def token_budget_left(conn: AsyncConnection) -> int:
    """Return today's remaining token budget (huge when unlimited)."""
    limit = get_config().budgets.tokens_per_day
    if limit is None:
        return UNLIMITED
    return limit - await tokens_used_today(conn)


async def job_chat_tokens_left(conn: AsyncConnection) -> int:
    """Return today's remaining Job Chat token budget."""
    used = await conn.scalar(
        select(func.coalesce(func.sum(llm_usage.c.input_tokens + llm_usage.c.output_tokens), 0)).where(
            llm_usage.c.created_at >= start_of_day(), llm_usage.c.task == "job_chat"
        )
    )
    return get_config().budgets.job_chat_tokens_per_day - int(used or 0)


# ---- jobs API ----------------------------------------------------------------------------------------


async def api_calls_today(conn: AsyncConnection) -> int:
    """Return the paid jobs-API calls made since local midnight."""
    total = await conn.scalar(
        select(func.count()).select_from(api_usage).where(api_usage.c.created_at >= start_of_day())
    )
    return int(total or 0)


async def api_quota_left(conn: AsyncConnection) -> int:
    """Return today's remaining jobs-API calls."""
    return get_config().caps.jobs_api_calls_per_day - await api_calls_today(conn)


# ---- cards -------------------------------------------------------------------------------------------


async def lock_capacity(conn: AsyncConnection) -> None:
    """Take the capacity lock until the transaction ends."""
    # Transaction-scoped; never held across an LLM, storage or MCP call.
    await conn.execute(text(f"SELECT pg_advisory_xact_lock({CAPACITY_LOCK})"))


async def _cards_today(conn: AsyncConnection) -> int:
    """Return how many cards were created since local midnight."""
    return int(
        await conn.scalar(select(func.count()).select_from(cards).where(cards.c.created_at >= start_of_day()))
        or 0
    )


async def card_limit(conn: AsyncConnection) -> int:
    """Return today's card limit: the user's preference within the config cap."""
    return min((await _prefs(conn)).cards_per_day, get_config().caps.cards_per_day)


async def _live_reservations(conn: AsyncConnection) -> list[RowMapping]:
    """Return card reservations that haven't expired."""
    rows = await conn.execute(select(card_reservations).where(card_reservations.c.until > clock.now()))
    return list(rows.mappings())


def _until() -> datetime:
    """Return when a new or renewed reservation should expire."""
    return clock.now() + timedelta(seconds=get_config().lease.work_item_ttl_seconds)


async def reserve_card(conn: AsyncConnection, job_id: int) -> str:
    """Reserve daily capacity for building one job's card.

    Rebuilding an existing card is allowed beyond the cap; a new card
    counts against today's limit together with other builds in flight.

    Args:
        conn: An open transaction.
        job_id: The job about to be carded.

    Returns:
        The reservation token, needed to check or release it.

    Raises:
        CapReached: A build for this job is running, or the limit is
            hit.
    """
    await lock_capacity(conn)
    # Expired reservations belong to crashed builds: free them.
    await conn.execute(delete(card_reservations).where(card_reservations.c.until <= clock.now()))
    live = await _live_reservations(conn)
    if any(r["job_id"] == job_id for r in live):
        raise CapReached("a resume build is already running for this job")
    existing = await conn.scalar(select(cards.c.id).where(cards.c.job_id == job_id))
    if existing is None:
        # Builds in flight for new cards count as used capacity.
        pending = sum(not r["existing_card"] for r in live)
        if await _cards_today(conn) + pending >= await card_limit(conn):
            raise CapReached("daily card limit reached", retry_at=next_day())
    token = uuid4().hex
    await conn.execute(
        insert(card_reservations).values(
            job_id=job_id, token=token, existing_card=existing is not None, until=_until()
        )
    )
    return token


async def check_card_reservation(conn: AsyncConnection, job_id: int, token: str) -> None:
    """Confirm the reservation still holds right before the card is saved.

    Raises:
        CapReached: The reservation expired or was taken over, or the
            limit was reached meanwhile.
    """
    await lock_capacity(conn)
    held = (
        (await conn.execute(select(card_reservations).where(card_reservations.c.job_id == job_id)))
        .mappings()
        .one_or_none()
    )
    if held is None or held["token"] != token or held["until"] <= clock.now():
        raise CapReached("card reservation expired; retry the job")
    existing = await conn.scalar(select(cards.c.id).where(cards.c.job_id == job_id))
    if existing is None and await _cards_today(conn) >= await card_limit(conn):
        raise CapReached("daily card limit changed or was reached", retry_at=next_day())


async def release_card(conn: AsyncConnection, job_id: int, token: str) -> None:
    """Delete this build's reservation (after saving or on failure)."""
    await conn.execute(
        delete(card_reservations).where(
            card_reservations.c.job_id == job_id, card_reservations.c.token == token
        )
    )


async def renew_card(job_id: int, token: str) -> None:
    """Keep a card reservation alive while the (long) build runs. Cancelled by the builder."""
    import asyncio

    from jobpilot.db.session import transaction

    cfg = get_config().lease
    while True:
        await asyncio.sleep(cfg.heartbeat_seconds)
        try:
            async with transaction() as conn:
                await check_card_reservation(conn, job_id, token)
                await conn.execute(
                    update(card_reservations)
                    .where(card_reservations.c.job_id == job_id, card_reservations.c.token == token)
                    .values(until=_until())
                )
        except CapReached:
            # Stop renewing; the final save will see the lost
            # reservation.
            return  # the insert transaction will reject an expired/lost reservation
        except Exception:
            log.exception("card reservation heartbeat failed for job %s", job_id)


# ---- emails ------------------------------------------------------------------------------------------


async def check_email_capacity(conn: AsyncConnection, job_id: int, *, outbox_id: int | None = None) -> None:
    """Raise if sending another email now would break a limit.

    At approval (``outbox_id`` None) pending sends count as
    reservations. At delivery (``outbox_id`` set) only sends already
    delivered today count, because this row is itself the reservation.

    Args:
        conn: An open transaction.
        job_id: The job the email is for.
        outbox_id: The send row being delivered, if at delivery.

    Raises:
        CapReached: The daily limit or the company rule blocks it.
    """
    await lock_capacity(conn)
    cfg = get_config()
    prefs = await _prefs(conn)
    sends = (outbox.c.kind == "gmail") & (outbox.c.payload["op"].as_string() == "send_draft")
    if cfg.caps.emails_per_day is not None:  # off by default (decision D4): no daily email limit
        delivered_today = (outbox.c.status == "delivered") & (outbox.c.delivered_at >= start_of_day())
        counted = sends & (outbox.c.status.in_(("pending", "dead")) | delivered_today)
        if outbox_id is not None:
            counted = sends & delivered_today
        used = int(await conn.scalar(select(func.count()).select_from(outbox).where(counted)) or 0)
        if used >= cfg.caps.emails_per_day:
            raise CapReached("daily email limit reached", retry_at=next_day())
    if not prefs.company_rule:  # the 30-day company rule is your setting, off by default (D4)
        return
    job = (await conn.execute(select(jobs).where(jobs.c.id == job_id))).mappings().one()
    window = cfg.outreach["company_window_days"]
    if await emails.company_sent_since(conn, job["company_norm"], clock.now() - timedelta(days=window)):
        raise CapReached(
            f"company email cooldown is on (an email to this company went out in the last {window} days)"
        )
    if outbox_id is None:
        pending = await conn.scalar(
            select(outbox.c.id)
            .join(jobs, jobs.c.id == outbox.c.payload["job_id"].as_integer())
            .where(
                sends, outbox.c.status.in_(("pending", "dead")), jobs.c.company_norm == job["company_norm"]
            )
            .limit(1)
        )
        if pending is not None:
            raise CapReached("an email to this company is already queued or needs reconciliation")
