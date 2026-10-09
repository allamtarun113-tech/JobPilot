"""Enqueue / claim / finish work items using SKIP LOCKED. Enqueue wakes the worker via NOTIFY."""

from __future__ import annotations

from typing import Any

from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.queries import outbox as outbox_repo
from jobpilot.db.queries import work_items as repo

CHANNEL = "work_items"


async def enqueue(
    conn: AsyncConnection, *, kind: str, payload: dict[str, Any], idempotency_key: str | None = None
) -> int | None:
    """Add a work item and wake the worker when the transaction commits.

    Args:
        conn: An open transaction.
        kind: The work kind (see ``models/work.py``).
        payload: JSON arguments for the handler.
        idempotency_key: Optional key that makes a repeat a no-op.

    Returns:
        The new item id, or None for a duplicate.
    """
    item_id = await repo.enqueue(conn, kind=kind, payload=payload, idempotency_key=idempotency_key)
    # Only a new item needs a wake-up; NOTIFY fires on commit.
    if item_id is not None:
        await outbox_repo.notify(conn, CHANNEL, kind)
    return item_id


async def claim(conn: AsyncConnection, *, holder: str, limit: int, ttl_seconds: float) -> list[RowMapping]:
    """Claim up to ``limit`` ready items for this worker (SKIP LOCKED)."""
    return await repo.claim(conn, holder=holder, limit=limit, ttl_seconds=ttl_seconds)


async def finish(conn: AsyncConnection, item_id: int, holder: str) -> None:
    """Mark a claimed item done."""
    await repo.finish(conn, item_id, holder)


async def fail(
    conn: AsyncConnection, item_id: int, holder: str, *, error: str, attempts: int, max_attempts: int
) -> None:
    """Record a failed attempt, retrying with exponential backoff.

    Args:
        conn: An open transaction.
        item_id: The failed item.
        holder: The worker that held it.
        error: The error text.
        attempts: Attempts made so far, including this one.
        max_attempts: After this many the item fails for good.
    """
    # 2, 4, 8 ... seconds, capped at five minutes.
    retry = None if attempts >= max_attempts else min(2**attempts, 300)
    await repo.fail(conn, item_id, holder, error=error, retry_in_seconds=retry)
