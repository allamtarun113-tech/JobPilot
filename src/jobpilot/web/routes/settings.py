"""Settings: kill switch, dispatcher pause / resume, stuck (dead) messages, system-health counters.

Every action is recorded as an event, like a tap. A dead Telegram message can be skipped here (its queue then
continues); a dead email send can't - an uncertain send must be reconciled against Gmail Sent first
(`make reconcile`), never skipped or resent blindly.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core import clock
from jobpilot.db.queries import flags
from jobpilot.db.queries import outbox as outbox_repo
from jobpilot.db.session import transaction
from jobpilot.monitoring import health
from jobpilot.safety import killswitch
from jobpilot.web import auth
from jobpilot.web.deps import db, flash, page, record_action, templates

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, conn: Annotated[AsyncConnection, Depends(db)]) -> Response:
    """Show the kill switch, dispatcher pause, stuck messages and health counters."""
    return templates.TemplateResponse(
        request,
        "settings.html",
        page(
            request,
            killswitch=await flags.get(conn, flags.KILLSWITCH) or {},
            paused=await flags.get(conn, flags.DISPATCHER_PAUSED) or {},
            dead=await outbox_repo.dead_heads(conn),
            counters=await health.counters(conn),
        ),
    )


@router.post("/settings/action", dependencies=[Depends(auth.require_csrf)])
async def settings_action(
    request: Request,
    nonce: Annotated[str, Form()],
    action: Annotated[str, Form()],
    outbox_id: Annotated[int | None, Form()] = None,
) -> Response:
    """Run one settings action (recorded as an event, like a tap)."""
    async with transaction() as conn:
        if not await record_action(conn, f"settings.{action}", nonce, {"outbox_id": outbox_id}):
            return RedirectResponse("/settings", status_code=303)
        if action == "killswitch_on":
            await killswitch.engage(conn, "manual (dashboard)")
            flash(request, "Kill switch ON.")
        elif action == "killswitch_off":
            await killswitch.release(conn)
            flash(request, "Kill switch OFF.")
        elif action in ("dispatcher_pause", "dispatcher_resume"):
            on = action == "dispatcher_pause"
            await flags.put(
                conn,
                flags.DISPATCHER_PAUSED,
                {"on": on, "reason": "dashboard", "at": clock.now().isoformat()},
            )
            flash(request, "Delivery paused." if on else "Delivery resumed.")
        elif action == "skip_dead" and outbox_id is not None:
            row = await outbox_repo.get(conn, outbox_id)
            if row is None or row["status"] != "dead":
                flash(request, "That message is no longer stuck.")
            # An uncertain email send may already be out: never skip it
            # blindly.
            elif row["kind"] != "telegram":
                flash(request, "A stuck email send must be reconciled with `make reconcile`, not skipped.")
            else:
                await outbox_repo.mark_skipped(conn, outbox_id, "skipped by you on the dashboard")
                flash(request, "Skipped; the queue continues.")
    return RedirectResponse("/settings", status_code=303)
