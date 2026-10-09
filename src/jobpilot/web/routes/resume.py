"""Resume page: the base resume PDF rendered from structured data, its parse check and evidence status."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.queries import resume_docs
from jobpilot.db.session import transaction
from jobpilot.integrations.storage import NotFound, get_store
from jobpilot.models.resume_doc import ResumeDoc
from jobpilot.models.work import RENDER_RESUME
from jobpilot.profile import confirm
from jobpilot.queue import jobs_queue
from jobpilot.web import auth
from jobpilot.web.deps import db, flash, page, record_action, templates

router = APIRouter()


@router.get("/resume", response_class=HTMLResponse)
async def resume_page(request: Request, conn: Annotated[AsyncConnection, Depends(db)]) -> Response:
    """Show the base resume, its parse check and evidence status."""
    row = await resume_docs.current(conn)
    doc = ResumeDoc.model_validate(row["doc"]) if row else None
    backing = await confirm.backing_report(conn, doc) if doc else None
    return templates.TemplateResponse(
        request, "resume.html", page(request, row=row, doc=doc, backing=backing)
    )


@router.post("/resume/render", dependencies=[Depends(auth.require_csrf)])
async def rerender(request: Request, nonce: Annotated[str, Form()]) -> Response:
    """Queue a re-render of the base resume PDF."""
    async with transaction() as conn:
        if await record_action(conn, "resume.render", nonce, {}):
            await jobs_queue.enqueue(conn, kind=RENDER_RESUME, payload={}, idempotency_key=f"render:{nonce}")
            flash(request, "Rendering - refresh in a few seconds.")
    return RedirectResponse("/resume", status_code=303)


@router.get("/files/{key:path}")
async def file(key: str) -> Response:
    """Serve a stored resume PDF (only keys under resumes/ ending in .pdf)."""
    if not key.startswith("resumes/") or not key.endswith(".pdf"):
        raise HTTPException(status_code=404)
    try:
        data = await get_store().get(key)
    except (NotFound, ValueError):
        return HTMLResponse(
            "<p>This resume PDF isn't in storage (for example after moving databases). "
            "Run <code>make rebuild-pdfs</code> on the laptop to rebuild it, then reload.</p>",
            status_code=404,
        )
    return Response(data, media_type="application/pdf", headers={"Cache-Control": "no-store"})
