"""The Prepare page: a job's interview pack, and known/weak marks."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.queries import prep_sets as prep_repo
from jobpilot.db.queries import requirements as req_repo
from jobpilot.db.queries import resume_versions as versions_repo
from jobpilot.db.queries import scores as scores_repo
from jobpilot.db.session import transaction
from jobpilot.models.work import PREP_REQUEST
from jobpilot.prep import marks
from jobpilot.queue import jobs_queue
from jobpilot.web import auth
from jobpilot.web.deps import db, flash, page, record_action, templates

router = APIRouter()


@router.get("/jobs/{job_id}/prepare", response_class=HTMLResponse)
async def prepare_page(
    request: Request, job_id: int, conn: Annotated[AsyncConnection, Depends(db)]
) -> Response:
    """Show a job's prep pack and what it was built from."""
    job = await jobs_repo.get(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404)
    card = await cards_repo.for_job(conn, job_id)
    score = await scores_repo.latest_for(conn, job_id)
    req = await req_repo.get(conn, score["requirements_id"]) if score else None
    approved = (
        await versions_repo.get(conn, card["approved_resume_version_id"])
        if card and card["approved_resume_version_id"]
        else None
    )
    prep = await prep_repo.get(conn, job_id)
    questions = list(prep["questions"] or []) if prep else []
    cited = {c for q in questions for c in q.get("citations", [])}
    built_from = {
        "must": list((req["data"] if req else {}).get("must_skills", [])),
        "nice": list((req["data"] if req else {}).get("nice_skills", [])),
        "gaps": [g["skill"] for g in (score["gap"] if score else [])],
        "resume_version": approved["version"] if approved else None,
        "snippet": bool(job["jd_is_snippet"]),
        "book_passages": sum(c.startswith("chunk:") for c in cited),
        "resume_items": sum(c.startswith("profile:") for c in cited),
        "web_notes": len(prep["research"] or []) if prep else 0,
    }
    working = bool(prep and prep["status"] in {"queued", "working"})
    return templates.TemplateResponse(
        request,
        "prepare.html",
        page(request, job=job, card=card, prep=prep, built_from=built_from, working=working),
    )


@router.post("/jobs/{job_id}/prepare", dependencies=[Depends(auth.require_csrf)])
async def request_prepare(request: Request, job_id: int, nonce: Annotated[str, Form()]) -> Response:
    """Queue building the prep pack (needs an approved resume)."""
    async with transaction() as conn:
        card = await cards_repo.for_job(conn, job_id)
        current = await prep_repo.get(conn, job_id)
        if card is None or card["approved_resume_version_id"] is None:
            flash(request, "Approve a resume before generating interview preparation.")
        elif current is not None and current["status"] in {"queued", "working"}:
            flash(request, "Preparation is already queued or running.")
        elif await record_action(conn, "prepare.generate", nonce, {"job_id": job_id}):
            prep = await prep_repo.enqueue(conn, job_id)
            await jobs_queue.enqueue(
                conn,
                kind=PREP_REQUEST,
                payload={"job_id": job_id},
                idempotency_key=f"prep:{job_id}:v{prep['version']}",
            )
            flash(request, "Preparation queued. Reload shortly for the generated set.")
    return RedirectResponse(f"/jobs/{job_id}/prepare", status_code=303)


@router.post("/jobs/{job_id}/prepare/questions/{question_id}", dependencies=[Depends(auth.require_csrf)])
async def mark_question(
    request: Request,
    job_id: int,
    question_id: str,
    nonce: Annotated[str, Form()],
    version: Annotated[int, Form()],
    result: Annotated[str, Form()],
) -> Response:
    """Mark one prep question known or weak (version-checked)."""
    try:
        async with transaction() as conn:
            fresh = await record_action(
                conn, "prepare.mark", nonce, {"job_id": job_id, "question_id": question_id}
            )
        if fresh:
            await marks.mark(job_id, question_id, result == "known", version)
    except ValueError as exc:
        flash(request, str(exc))
    return RedirectResponse(f"/jobs/{job_id}/prepare", status_code=303)
