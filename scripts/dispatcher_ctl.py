"""Pause or resume the dispatcher (restore procedure), or show its state.

Usage: uv run python scripts/dispatcher_ctl.py pause|resume|status
"""

from __future__ import annotations

import asyncio
import sys

from jobpilot.core import clock
from jobpilot.db.queries import flags
from jobpilot.db.queries import outbox as outbox_repo
from jobpilot.db.session import dispose_engine, transaction


async def main(action: str) -> None:
    """Pause, resume or show the dispatcher, with outbox counts."""
    async with transaction() as conn:
        if action == "pause":
            await flags.put(conn, flags.DISPATCHER_PAUSED, {"on": True, "at": clock.now().isoformat()})
        elif action == "resume":
            await flags.put(conn, flags.DISPATCHER_PAUSED, {"on": False, "at": clock.now().isoformat()})
        paused = await flags.is_on(conn, flags.DISPATCHER_PAUSED)
        lag = await outbox_repo.lag(conn)
    print(
        f"dispatcher {'PAUSED' if paused else 'running'}; "
        f"outbox undelivered={lag['undelivered']} dead={lag['dead']}"
    )
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "status"))
