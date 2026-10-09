"""Profile page: confirm, edit and add the facts everything rests on.

Each experience and each project with its own fields and details, then
education, skills and activities. Confirm, edit or reject the draft
items; add experiences, projects and details; add notes.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core.config import get_settings
from jobpilot.core.errors import VersionConflict
from jobpilot.db.queries import resume_docs
from jobpilot.db.session import transaction
from jobpilot.matching.profile_view import months_between
from jobpilot.models.profile import DETAIL_KINDS, ENTRY_FIELDS
from jobpilot.models.resume_doc import ResumeDoc
from jobpilot.models.work import PROFILE_NOTES
from jobpilot.profile import confirm, structure
from jobpilot.queue import jobs_queue
from jobpilot.web import auth
from jobpilot.web.deps import db, flash, page, record_action, templates

router = APIRouter()


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, conn: Annotated[AsyncConnection, Depends(db)]) -> Response:
    """Show the profile laid out by experience and project, with counts."""
    layout = await structure.layout(conn)
    row = await resume_docs.current(conn)
    backing = await confirm.backing_report(conn, ResumeDoc.model_validate(row["doc"])) if row else None
    everything = [
        *layout.summary,
        *(i for e in layout.entries for i in (e.item, *e.details)),
        *layout.unattached,
        *(i for rows in layout.skills.values() for i in rows),
        *(i for _, rows in layout.others for i in rows),
    ]
    counts = {s: sum(1 for i in everything if i["status"] == s) for s in ("draft", "confirmed", "rejected")}
    return templates.TemplateResponse(
        request,
        "profile.html",
        page(
            request,
            layout=layout,
            counts=counts,
            backing=backing,
            fields=ENTRY_FIELDS,
            months={e.item["id"]: months_between(e.fields.get("dates")) for e in layout.experiences},
            has_resume=row is not None,
            llm_ready=bool(get_settings().openai_api_key),
        ),
    )


@router.post("/profile/confirm-all", dependencies=[Depends(auth.require_csrf)])
async def confirm_all(request: Request, nonce: Annotated[str, Form()]) -> Response:
    """Confirm every draft item at once."""
    async with transaction() as conn:
        if await record_action(conn, "profile.confirm_all", nonce, {}):
            n = await confirm.confirm_all(conn)
            flash(request, f"Confirmed {n} item(s).")
    return RedirectResponse("/profile", status_code=303)


@router.post("/profile/items/{item_id}", dependencies=[Depends(auth.require_csrf)])
async def update_item(
    request: Request,
    item_id: int,
    nonce: Annotated[str, Form()],
    version: Annotated[int, Form()],
    action: Annotated[str, Form()],
    text: Annotated[str | None, Form()] = None,
    parent_id: Annotated[str | None, Form()] = None,
) -> Response:
    """Edit, confirm, reject or move one profile item (version-checked)."""
    try:
        async with transaction() as conn:
            payload = {"item_id": item_id, "version": version, "action": action}
            if await record_action(conn, f"profile.{action}", nonce, payload):
                if action == "edit" and text is not None:
                    await confirm.edit_text(conn, item_id, version, text)
                elif action in ("confirmed", "rejected", "draft"):
                    await confirm.set_status(conn, item_id, version, action)
                elif action == "move":
                    target = int(parent_id) if parent_id else None
                    await structure.move_detail(conn, item_id, version, target)
    except VersionConflict:
        flash(request, "That item changed in the meantime - here is the current version.")
    except ValueError as exc:
        flash(request, str(exc))
    return RedirectResponse(f"/profile#item-{item_id}", status_code=303)


async def _form_values(request: Request) -> dict[str, str]:
    """The submitted fields (only the experience / project ones are read)."""
    return {key: value for key, value in (await request.form()).items() if isinstance(value, str)}


@router.post("/profile/entries", dependencies=[Depends(auth.require_csrf)])
async def add_entry(
    request: Request, nonce: Annotated[str, Form()], kind: Annotated[str, Form()]
) -> Response:
    """A new experience or project, typed here."""
    anchor = ""
    try:
        values = await _form_values(request)
        async with transaction() as conn:
            if await record_action(conn, f"profile.add_{kind}", nonce, {"kind": kind}):
                anchor = f"#item-{await structure.add_entry(conn, kind, values)}"
                flash(request, "Experience added." if kind == "role" else "Project added.")
    except ValueError as exc:
        flash(request, f"Not added: {exc}.")
    return RedirectResponse(f"/profile{anchor}", status_code=303)


@router.post("/profile/items/{item_id}/fields", dependencies=[Depends(auth.require_csrf)])
async def save_fields(
    request: Request, item_id: int, nonce: Annotated[str, Form()], version: Annotated[int, Form()]
) -> Response:
    """The fields of one experience or project."""
    try:
        values = await _form_values(request)
        async with transaction() as conn:
            if await record_action(conn, "profile.fields", nonce, {"item_id": item_id, "version": version}):
                await structure.save_fields(conn, item_id, version, values)
                flash(request, "Saved.")
    except VersionConflict:
        flash(request, "That entry changed in the meantime - here is the current version.")
    except ValueError as exc:
        flash(request, f"Not saved: {exc}.")
    return RedirectResponse(f"/profile#item-{item_id}", status_code=303)


@router.post("/profile/items/{item_id}/details", dependencies=[Depends(auth.require_csrf)])
async def add_detail(
    request: Request,
    item_id: int,
    nonce: Annotated[str, Form()],
    text: Annotated[str, Form()],
    kind: Annotated[str, Form()] = "achievement",
) -> Response:
    """A detail (what you did, a result) under one experience or project."""
    try:
        async with transaction() as conn:
            if await record_action(conn, "profile.add_detail", nonce, {"parent_id": item_id, "kind": kind}):
                await structure.add_detail(conn, item_id, kind if kind in DETAIL_KINDS else "", text)
    except ValueError as exc:
        flash(request, f"Not added: {exc}.")
    return RedirectResponse(f"/profile#item-{item_id}", status_code=303)


@router.post("/profile/notes", dependencies=[Depends(auth.require_csrf)])
async def add_notes(
    request: Request, nonce: Annotated[str, Form()], notes: Annotated[str, Form()]
) -> Response:
    """Queue free-form notes to be turned into draft profile items."""
    notes = notes.strip()
    if not notes:
        return RedirectResponse("/profile", status_code=303)
    if not get_settings().openai_api_key:
        flash(request, "Reading notes needs OPENAI_API_KEY in .env (added at the end of the build).")
        return RedirectResponse("/profile", status_code=303)
    async with transaction() as conn:
        if await record_action(conn, "profile.notes", nonce, {"chars": len(notes)}):
            await jobs_queue.enqueue(
                conn,
                kind=PROFILE_NOTES,
                payload={"notes": notes, "nonce": nonce},
                idempotency_key=f"notes:{nonce}",
            )
            flash(request, "Reading your notes - new draft items appear here in a few seconds.")
    return RedirectResponse("/profile", status_code=303)
