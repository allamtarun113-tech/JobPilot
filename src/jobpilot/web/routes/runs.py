"""Runs tab: recent runs with status, step, stop reason, tokens, cost and trace id."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.queries import runs as runs_repo
from jobpilot.monitoring.tracing import trace_url
from jobpilot.web.deps import db, page, templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
@router.get("/runs", response_class=HTMLResponse)
async def runs_page(request: Request, conn: Annotated[AsyncConnection, Depends(db)]) -> HTMLResponse:
    """Show recent runs with links into their traces (also the home page)."""
    rows = await runs_repo.latest(conn, limit=50)
    links = {r["id"]: trace_url(r["trace_id"]) for r in rows}
    return templates.TemplateResponse(request, "runs.html", page(request, runs=rows, trace_links=links))
