"""SQL for emails (HR drafts) and sent_emails (one per job)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core import clock
from jobpilot.core.errors import VersionConflict
from jobpilot.db.tables import emails, jobs, sent_emails


async def create(conn: AsyncConnection, **values: Any) -> int:
    """Insert an email draft and return its id."""
    return int((await conn.execute(insert(emails).values(**values).returning(emails.c.id))).scalar_one())


async def get(conn: AsyncConnection, email_id: int) -> RowMapping | None:
    """Return one email row, or None."""
    return (await conn.execute(select(emails).where(emails.c.id == email_id))).mappings().one_or_none()


async def latest(conn: AsyncConnection, job_id: int) -> RowMapping | None:
    """Return the newest email version for a job, or None."""
    return (
        (
            await conn.execute(
                select(emails).where(emails.c.job_id == job_id).order_by(emails.c.version.desc()).limit(1)
            )
        )
        .mappings()
        .one_or_none()
    )


async def _locked_update(conn: AsyncConnection, email_id: int, expected_lock: int, **values: Any) -> int:
    """Version-checked update ('where lock_version = n'); raises VersionConflict on a lost update."""
    result = await conn.execute(
        update(emails)
        .where(emails.c.id == email_id, emails.c.lock_version == expected_lock)
        .values(lock_version=emails.c.lock_version + 1, updated_at=clock.now(), **values)
        .returning(emails.c.lock_version)
    )
    new = result.scalar_one_or_none()
    if new is None:
        raise VersionConflict(f"emails {email_id}: expected lock version {expected_lock}")
    return int(new)


async def set_draft_id(
    conn: AsyncConnection, email_id: int, draft_id: str, expected_lock: int, thread_id: str | None = None
) -> int:
    """Store the Gmail draft (and thread) id of an email.

    Returns:
        The new lock version.
    """
    return await _locked_update(
        conn, email_id, expected_lock, gmail_draft_id=draft_id, gmail_thread_id=thread_id
    )


async def set_status(conn: AsyncConnection, email_id: int, status: str, expected_lock: int) -> int:
    """Change an email's status (draft, sending, sent, skipped).

    Returns:
        The new lock version.
    """
    return await _locked_update(conn, email_id, expected_lock, status=status)


async def record_sent(conn: AsyncConnection, job_id: int, email_id: int, message_id: str) -> bool:
    """Record the sent message for a job, at most once.

    Args:
        conn: An open transaction.
        job_id: The job (UNIQUE in sent_emails).
        email_id: The email that was sent.
        message_id: Gmail's message id.

    Returns:
        False if an email for this job was already recorded.
    """
    stmt = (
        pg_insert(sent_emails)
        .values(job_id=job_id, email_id=email_id, gmail_message_id=message_id, sent_at=clock.now())
        # job_id is unique: a second send for the same job is ignored.
        .on_conflict_do_nothing(index_elements=[sent_emails.c.job_id])
    )
    return (await conn.execute(stmt)).rowcount == 1


async def sent_for_job(conn: AsyncConnection, job_id: int) -> RowMapping | None:
    """Return the sent-email row for a job, or None."""
    return (
        (await conn.execute(select(sent_emails).where(sent_emails.c.job_id == job_id)))
        .mappings()
        .one_or_none()
    )


async def company_sent_since(conn: AsyncConnection, company_norm: str, since: datetime) -> RowMapping | None:
    """Return any email sent to a company since ``since``, or None.

    Used by the optional one-email-per-company-per-30-days rule.
    """
    return (
        (
            await conn.execute(
                select(sent_emails)
                .join(jobs, jobs.c.id == sent_emails.c.job_id)
                .where(jobs.c.company_norm == company_norm, sent_emails.c.sent_at >= since)
                .limit(1)
            )
        )
        .mappings()
        .one_or_none()
    )


async def known_address(conn: AsyncConnection, company_norm: str) -> RowMapping | None:
    """The newest address an email for this company already used (found earlier, or given by you)."""
    return (
        (
            await conn.execute(
                select(emails.c.to_address, emails.c.to_source)
                .join(jobs, jobs.c.id == emails.c.job_id)
                .where(jobs.c.company_norm == company_norm, emails.c.to_address != "")
                .order_by(emails.c.id.desc())
                .limit(1)
            )
        )
        .mappings()
        .one_or_none()
    )
