"""Health endpoints.

Public /health says only whether the database answers (the URL is on
the internet); the details (dispatcher state, outbox lag, active run)
are at /health/details behind login.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core.config import get_config, get_settings
from jobpilot.db.queries import flags
from jobpilot.db.queries import outbox as outbox_repo
from jobpilot.db.queries import runs as runs_repo
from jobpilot.web.deps import db

router = APIRouter()


@router.get("/health")
async def health(conn: Annotated[AsyncConnection, Depends(db)]) -> dict[str, Any]:
    """Return ok when the database answers (public)."""
    await conn.execute(text("SELECT 1"))
    return {"ok": True}


@router.get("/health/details")
async def health_details(conn: Annotated[AsyncConnection, Depends(db)]) -> dict[str, Any]:
    """Return dispatcher, kill switch, outbox and run state (needs login)."""
    await conn.execute(text("SELECT 1"))
    active = await runs_repo.get_active(conn)
    lag = await outbox_repo.lag(conn)
    return {
        "ok": True,
        "shadow_mode": get_config().shadow_mode,
        "telegram": "dry-run" if get_settings().telegram_dry_run else "live",
        "dispatcher_paused": await flags.is_on(conn, flags.DISPATCHER_PAUSED),
        "killswitch": await flags.is_on(conn, flags.KILLSWITCH),
        "active_run": active["id"] if active else None,
        "outbox": {"undelivered": lag["undelivered"], "dead": lag["dead"]},
    }
