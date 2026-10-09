"""Append events (taps, chat messages, dashboard actions, /start) with an idempotency key."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.queries import events as events_repo


async def append(
    conn: AsyncConnection,
    *,
    type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    telegram_update_id: int | None = None,
) -> int | None:
    """Store an event unless its idempotency key was seen before.

    Args:
        conn: An open transaction.
        type: The event type, e.g. ``tap`` or ``command``.
        payload: The event data.
        idempotency_key: Makes a repeat of the same event a no-op.
        telegram_update_id: Telegram's update id, when the event came
            from Telegram.

    Returns:
        The new event id, or None if this event was already stored.
    """
    return await events_repo.append(
        conn,
        type=type,
        payload=payload,
        idempotency_key=idempotency_key,
        telegram_update_id=telegram_update_id,
    )
