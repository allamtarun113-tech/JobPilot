"""Write outbox rows inside the caller's transaction. Features call this instead of any MCP send.

Rows carry a partition key and a sequence number; the dispatcher delivers each partition strictly in order
and pauses a partition whose head row fails. Ordering only matters within one card (an edit must not
overtake its message) and within one run (started -> summary), so those share a partition; every other
message (replies, alerts) gets its own, and one failure can never block the rest.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.queries import outbox as repo

CHANNEL = "outbox"


def card_partition(card_id: int) -> str:
    """Return the outbox partition key for a card's messages."""
    return f"card:{card_id}"


def run_partition(run_id: int) -> str:
    """Return the outbox partition key for a run's messages."""
    return f"run:{run_id}"


async def enqueue(
    conn: AsyncConnection,
    *,
    partition_key: str,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
) -> int:
    """Add a row in the caller's transaction and wake the dispatcher on commit.

    Returns the row id. Writing the same idempotency key twice returns the existing row.
    """
    # Send tokens are minted at delivery time and must never be stored.
    if any("token" in key.lower() for key in payload):
        raise ValueError("outbox payloads never hold tokens")
    # Same key twice (a retried transaction): reuse the first row.
    existing = await repo.find_by_key(conn, idempotency_key)
    if existing is not None:
        return existing
    seq = await repo.next_seq(conn, partition_key)
    row_id = await repo.insert_row(
        conn,
        partition_key=partition_key,
        seq=seq,
        kind=kind,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    if row_id is None:  # a concurrent writer inserted the same key first
        found = await repo.find_by_key(conn, idempotency_key)
        assert found is not None
        return found
    await repo.notify(conn, CHANNEL, partition_key)
    return row_id


async def telegram_message(
    conn: AsyncConnection,
    text: str,
    *,
    idempotency_key: str,
    partition_key: str | None = None,
    reply_markup: dict[str, Any] | None = None,
    parse_mode: str | None = None,
) -> int:
    """Queue a Telegram text message.

    A standalone message gets its own partition unless the caller
    needs ordering (card, run).

    Args:
        conn: An open transaction.
        text: The message text (cut to Telegram's 4,096 characters).
        idempotency_key: Makes a repeat a no-op.
        partition_key: The partition to order it within, if any.
        reply_markup: Inline keyboard buttons, if any.
        parse_mode: ``HTML`` for formatted text.

    Returns:
        The outbox row id.
    """
    payload: dict[str, Any] = {"op": "send_message", "text": text[:4096]}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return await enqueue(
        conn,
        partition_key=partition_key or f"msg:{idempotency_key}",
        kind="telegram",
        payload=payload,
        idempotency_key=idempotency_key,
    )


async def telegram_edit(
    conn: AsyncConnection,
    text: str,
    *,
    target_outbox_id: int,
    idempotency_key: str,
    partition_key: str,
    reply_markup: dict[str, Any] | None = None,
    parse_mode: str | None = None,
) -> int:
    """Edit the message another outbox row sent. The Telegram message id is resolved at delivery time."""
    payload: dict[str, Any] = {"op": "edit_message", "text": text, "target_outbox_id": target_outbox_id}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return await enqueue(
        conn, partition_key=partition_key, kind="telegram", payload=payload, idempotency_key=idempotency_key
    )
