"""SQL for system_flags (kill switch, dispatcher paused)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core import clock
from jobpilot.db.tables import system_flags

KILLSWITCH = "killswitch"
DISPATCHER_PAUSED = "dispatcher_paused"


async def get(conn: AsyncConnection, key: str) -> dict[str, Any] | None:
    """Return a system flag's JSON value, or None when unset."""
    row = await conn.execute(select(system_flags.c.value).where(system_flags.c.key == key))
    return row.scalar_one_or_none()


async def put(conn: AsyncConnection, key: str, value: dict[str, Any]) -> None:
    """Set a system flag, creating it if needed."""
    stmt = insert(system_flags).values(key=key, value=value, updated_at=clock.now())
    await conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[system_flags.c.key],
            set_={"value": value, "version": system_flags.c.version + 1, "updated_at": clock.now()},
        )
    )


async def is_on(conn: AsyncConnection, key: str) -> bool:
    """Return True when the flag exists and its ``on`` field is true."""
    value = await get(conn, key)
    return bool(value and value.get("on"))
