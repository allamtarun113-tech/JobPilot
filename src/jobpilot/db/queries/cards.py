"""Version-checked card persistence."""

from __future__ import annotations

from typing import Any

from sqlalchemy import insert, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.queries import outbox as outbox_repo
from jobpilot.db.queries.base import versioned_update
from jobpilot.db.tables import cards, jobs


async def create(conn: AsyncConnection, *, job_id: int, callback_token: str) -> int:
    """Insert a new card for a job.

    Args:
        conn: An open transaction.
        job_id: The job the card shows.
        callback_token: Short opaque id carried by the card's buttons.

    Returns:
        The new card id.
    """
    row = await conn.execute(
        insert(cards).values(job_id=job_id, callback_token=callback_token).returning(cards.c.id)
    )
    return int(row.scalar_one())


async def get(conn: AsyncConnection, card_id: int) -> RowMapping | None:
    """Return one card row, or None."""
    return (await conn.execute(select(cards).where(cards.c.id == card_id))).mappings().one_or_none()


async def for_job(conn: AsyncConnection, job_id: int) -> RowMapping | None:
    """Return the card of a job, or None (one card per job)."""
    return (await conn.execute(select(cards).where(cards.c.job_id == job_id))).mappings().one_or_none()


async def by_token(conn: AsyncConnection, token: str) -> RowMapping | None:
    """Return the card whose buttons carry ``token``, or None."""
    return (await conn.execute(select(cards).where(cards.c.callback_token == token))).mappings().one_or_none()


async def update(conn: AsyncConnection, card_id: int, expected_version: int, **values: Any) -> int:
    """Version-checked card update.

    Returns:
        The new version.

    Raises:
        VersionConflict: The card changed since it was read.
    """
    return await versioned_update(conn, cards, card_id, expected_version, **values)


async def by_telegram_message(conn: AsyncConnection, message_id: int) -> RowMapping | None:
    """The card any delivered Telegram message belongs to (the card, its PDFs, previews and proposals)."""
    card_id = await outbox_repo.card_for_message(conn, message_id)
    return await get(conn, card_id) if card_id is not None else None


async def set_telegram_message_id(conn: AsyncConnection, card_id: int, message_id: int) -> None:
    """Delivery metadata, not card state: written without a version bump so it never races a tap."""
    await conn.execute(sa_update(cards).where(cards.c.id == card_id).values(telegram_message_id=message_id))


async def recent_with_names(conn: AsyncConnection, limit: int = 200) -> list[dict[str, Any]]:
    """Recent cards with their company / title (and normalized forms) for whole-name matching."""
    rows = await conn.execute(
        select(cards, jobs.c.company, jobs.c.title, jobs.c.company_norm, jobs.c.title_norm)
        .join(jobs, jobs.c.id == cards.c.job_id)
        .order_by(cards.c.updated_at.desc())
        .limit(limit)
    )
    return [dict(r) for r in rows.mappings()]


async def search(conn: AsyncConnection, query: str, limit: int = 3) -> list[RowMapping]:
    """Find recent cards whose company or title contains ``query``.

    Args:
        conn: An open connection.
        query: Free text, matched case-insensitively.
        limit: The most rows to return.

    Returns:
        Card rows with company and title, newest first.
    """
    term = f"%{query.strip()}%"
    rows = await conn.execute(
        select(cards, jobs.c.company, jobs.c.title)
        .join(jobs, jobs.c.id == cards.c.job_id)
        .where(or_(jobs.c.company.ilike(term), jobs.c.title.ilike(term)))
        .order_by(cards.c.updated_at.desc())
        .limit(limit)
    )
    return list(rows.mappings())


async def recent(conn: AsyncConnection, limit: int = 3) -> list[RowMapping]:
    """Return the most recently updated cards with company and title."""
    rows = await conn.execute(
        select(cards, jobs.c.company, jobs.c.title)
        .join(jobs, jobs.c.id == cards.c.job_id)
        .order_by(cards.c.updated_at.desc())
        .limit(limit)
    )
    return list(rows.mappings())
