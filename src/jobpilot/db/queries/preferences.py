"""SQL for preferences (one versioned row per user)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.queries.base import versioned_update
from jobpilot.db.tables import USER_ID, preferences


async def get(conn: AsyncConnection) -> RowMapping | None:
    """Return the preferences row, or None before first use."""
    return (await conn.execute(select(preferences))).mappings().one_or_none()


async def ensure(conn: AsyncConnection, defaults: dict[str, Any]) -> RowMapping:
    """Create the preferences row with defaults if missing, then return it."""
    await conn.execute(insert(preferences).values(user_id=USER_ID, data=defaults).on_conflict_do_nothing())
    row = await get(conn)
    assert row is not None
    return row


async def save(conn: AsyncConnection, expected_version: int, data: dict[str, Any]) -> int:
    """Version-checked save of the preferences data.

    Returns:
        The new version.
    """
    return await versioned_update(conn, preferences, USER_ID, expected_version, data=data)
