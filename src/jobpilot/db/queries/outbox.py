"""SQL for the outbox and its per-partition sequence numbers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core import clock
from jobpilot.db.tables import outbox, outbox_partitions

# Rows still waiting for delivery; a dead row blocks its partition.
UNDELIVERED = ("pending", "dead")


async def find_by_key(conn: AsyncConnection, idempotency_key: str) -> int | None:
    """Return the id of the outbox row with this key, or None."""
    row = await conn.execute(select(outbox.c.id).where(outbox.c.idempotency_key == idempotency_key))
    return row.scalar_one_or_none()


async def next_seq(conn: AsyncConnection, partition_key: str) -> int:
    """Take the partition's next sequence number atomically.

    The row lock is held until commit, so sequence order matches
    commit order within a partition.

    Returns:
        The new sequence number (1 for a new partition).
    """
    stmt = insert(outbox_partitions).values(partition_key=partition_key, last_seq=1)
    upsert = stmt.on_conflict_do_update(
        index_elements=[outbox_partitions.c.partition_key],
        set_={"last_seq": outbox_partitions.c.last_seq + 1},
    ).returning(outbox_partitions.c.last_seq)
    return int((await conn.execute(upsert)).scalar_one())


async def insert_row(
    conn: AsyncConnection,
    *,
    partition_key: str,
    seq: int,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
) -> int | None:
    """Insert an outbox row unless its idempotency key exists.

    Returns:
        The new row id, or None for a duplicate.
    """
    stmt = (
        insert(outbox)
        .values(
            partition_key=partition_key,
            seq=seq,
            kind=kind,
            payload=payload,
            idempotency_key=idempotency_key,
            next_attempt_at=clock.now(),
        )
        .on_conflict_do_nothing(index_elements=[outbox.c.idempotency_key])
        .returning(outbox.c.id)
    )
    return (await conn.execute(stmt)).scalar_one_or_none()


async def notify(conn: AsyncConnection, channel: str, payload: str = "") -> None:
    """Send a Postgres NOTIFY (delivered when the transaction commits)."""
    await conn.execute(select(func.pg_notify(channel, payload)))


async def partition_heads(conn: AsyncConnection) -> list[RowMapping]:
    """The lowest undelivered row of every partition. A dead head keeps its partition paused."""
    stmt = (
        select(outbox)
        .where(outbox.c.status.in_(UNDELIVERED))
        .order_by(outbox.c.partition_key, outbox.c.seq)
        .ext(distinct_on(outbox.c.partition_key))
    )
    return list((await conn.execute(stmt)).mappings())


async def get(conn: AsyncConnection, row_id: int) -> RowMapping | None:
    """Return one outbox row, or None."""
    return (await conn.execute(select(outbox).where(outbox.c.id == row_id))).mappings().one_or_none()


async def mark_delivered(conn: AsyncConnection, row_id: int, external_id: str | None) -> bool:
    """Mark a pending row delivered and store the external message id.

    Returns:
        False if the row was no longer pending.
    """
    now = clock.now()
    result = await conn.execute(
        update(outbox)
        .where(outbox.c.id == row_id, outbox.c.status == "pending")
        .values(
            status="delivered",
            external_id=external_id,
            delivered_at=now,
            attempts=outbox.c.attempts + 1,
            last_error=None,
            updated_at=now,
        )
    )
    return result.rowcount == 1


async def mark_failed(
    conn: AsyncConnection,
    row_id: int,
    *,
    error: str,
    next_attempt_at: datetime | None,
    count_attempt: bool = True,
) -> None:
    """Schedule a retry, or mark the row dead.

    Args:
        conn: An open transaction.
        row_id: The outbox row.
        error: The error text (truncated to 2,000 characters).
        next_attempt_at: When to retry; None marks the row dead.
        count_attempt: False for a deferral (cap, kill switch), which
            doesn't use up an attempt.
    """
    values: dict[str, Any] = {
        "attempts": outbox.c.attempts + (1 if count_attempt else 0),
        "last_error": error[:2000],
        "updated_at": clock.now(),
    }
    if next_attempt_at is None:
        values["status"] = "dead"
    else:
        values["next_attempt_at"] = next_attempt_at
    await conn.execute(
        update(outbox).where(outbox.c.id == row_id, outbox.c.status == "pending").values(**values)
    )


async def next_due_at(conn: AsyncConnection) -> datetime | None:
    """Return when the next pending row is due, or None if none is."""
    row = await conn.execute(select(func.min(outbox.c.next_attempt_at)).where(outbox.c.status == "pending"))
    return row.scalar_one_or_none()


async def lag(conn: AsyncConnection) -> dict[str, Any]:
    """For /status and health: undelivered count and the age of the oldest pending row."""
    row = await conn.execute(
        select(func.count(), func.min(outbox.c.created_at)).where(outbox.c.status.in_(UNDELIVERED))
    )
    count, oldest = row.one()
    dead = await conn.execute(select(func.count()).where(outbox.c.status == "dead"))
    return {"undelivered": count, "oldest_created_at": oldest, "dead": dead.scalar_one()}


async def mark_skipped(conn: AsyncConnection, row_id: int, reason: str) -> None:
    """Mark an undelivered row skipped, with the reason."""
    await conn.execute(
        update(outbox)
        .where(outbox.c.id == row_id, outbox.c.status.in_(("pending", "dead")))
        .values(status="skipped", last_error=reason, updated_at=clock.now())
    )


async def supersede_email_sends(
    conn: AsyncConnection, email_id: int, reason: str = "reconfirmed with a fresh approval"
) -> None:
    """Skip every undelivered send of an email.

    Called when the email is re-approved, so only the newest approval
    can send it.
    """
    await conn.execute(
        update(outbox)
        .where(
            outbox.c.kind == "gmail",
            outbox.c.payload["op"].as_string() == "send_draft",
            outbox.c.payload["email_id"].as_integer() == email_id,
            outbox.c.status.in_(("pending", "dead")),
        )
        .values(status="skipped", last_error=reason, updated_at=clock.now())
    )


async def dead_heads(conn: AsyncConnection) -> list[RowMapping]:
    """Partitions whose head row is dead (delivery for that partition is paused until someone acts)."""
    return [row for row in await partition_heads(conn) if row["status"] == "dead"]


async def oldest_pending_age_seconds(conn: AsyncConnection) -> float | None:
    """Return how overdue the oldest due row is, in seconds.

    Returns:
        None when nothing is due (used by the outbox-lag alert).
    """
    oldest = await conn.scalar(
        select(func.min(outbox.c.next_attempt_at)).where(
            outbox.c.status == "pending", outbox.c.next_attempt_at <= clock.now()
        )
    )
    return (clock.now() - oldest).total_seconds() if oldest else None


async def card_for_message(conn: AsyncConnection, message_id: int) -> int | None:
    """The card a delivered Telegram message belongs to (cards, resume PDFs, previews, proposals)."""
    rows = await conn.execute(
        select(outbox.c.partition_key)
        .where(
            outbox.c.kind == "telegram",
            outbox.c.external_id == str(message_id),
            outbox.c.partition_key.like("card:%"),
        )
        .order_by(outbox.c.id.desc())
        .limit(1)
    )
    key = rows.scalar_one_or_none()
    # Partition keys look like "card:<id>".
    return int(key.split(":", 1)[1]) if key else None
