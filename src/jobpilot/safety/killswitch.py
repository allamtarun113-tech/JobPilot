"""The global stop flag: engaged by hand or automatically on error spikes / budget breach."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core import clock
from jobpilot.db.queries import flags


async def is_engaged(conn: AsyncConnection) -> bool:
    """Return True while the kill switch is on."""
    return await flags.is_on(conn, flags.KILLSWITCH)


async def engage(conn: AsyncConnection, reason: str) -> None:
    """Turn the kill switch on, recording why and when."""
    await flags.put(conn, flags.KILLSWITCH, {"on": True, "reason": reason, "at": clock.now().isoformat()})


async def release(conn: AsyncConnection) -> None:
    """Turn the kill switch off."""
    await flags.put(conn, flags.KILLSWITCH, {"on": False})
