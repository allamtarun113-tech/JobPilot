"""Jobs pages: the card list, the pipeline view and the job page.

One row per carded job (filter by card status or outcome) with the
metrics strip; a pipeline view of every job found; the job page with
card actions, the email editor, chat history and the full JD.

Dashboard actions are recorded as events (a nonce makes a double
submit a no-op), exactly like taps.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.cards import states, transitions
from jobpilot.core.errors import VersionConflict
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import chat_messages as chat_repo
from jobpilot.db.queries import emails as emails_repo
from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.queries import profile_items
from jobpilot.db.queries import proposals as proposals_repo
from jobpilot.db.queries import requirements as req_repo
from jobpilot.db.queries import resume_versions as versions_repo
from jobpilot.db.queries import scores as scores_repo
from jobpilot.db.session import transaction
from jobpilot.db.tables import cards as cards_table
from jobpilot.db.tables import jobs as jobs_table
from jobpilot.db.tables import resume_doc
from jobpilot.hr_email import drafts as email_drafts
from jobpilot.matching.gap_resources import for_skill, learning_plan
from jobpilot.models.email import EmailDraft
from jobpilot.models.job import PipelineStatus
from jobpilot.models.profile import fact_texts
from jobpilot.models.requirements import Requirements
from jobpilot.models.resume_doc import ResumeDoc
from jobpilot.models.work import EMAIL_DRAFT
from jobpilot.monitoring import metrics
from jobpilot.queue import jobs_queue
from jobpilot.resume import diff, skill_additions
from jobpilot.resume.comparison import compare, compare_tex
from jobpilot.safety import email_checks
from jobpilot.web import auth
from jobpilot.web.deps import db, flash, page, record_action, templates

router = APIRouter()


CARD_FILTERS = [
    "new",
    "resume_approved",
    "applied",
    "sending",
    "needs_reconfirm",
    "emailed",
    "skipped",
    "posting_closed",
    *states.OUTCOMES,
]


@router.get("/jobs", response_class=HTMLResponse)
async def cards_page(
    request: Request, conn: Annotated[AsyncConnection, Depends(db)], status: str | None = None
) -> Response:
    """One row per carded job, filterable by status (or outcome), with the metrics strip."""
    stmt = (
        select(
            cards_table,
            jobs_table.c.company,
            jobs_table.c.title,
            jobs_table.c.apply_url,
            jobs_table.c.city,
            jobs_table.c.jd_is_snippet,
        )
        .join(jobs_table, jobs_table.c.id == cards_table.c.job_id)
        .order_by(cards_table.c.updated_at.desc())
        .limit(300)
    )
    if status in states.OUTCOMES:
        stmt = stmt.where(cards_table.c.outcome == status)
    elif status in CARD_FILTERS:
        stmt = stmt.where(cards_table.c.status == status)
    else:
        status = None
    rows = [dict(r) for r in (await conn.execute(stmt)).mappings()]
    ids = [r["job_id"] for r in rows]
    scores = await scores_repo.latest_for_many(conn, ids)
    sources = await jobs_repo.source_counts(conn, ids)
    resumes = await versions_repo.latest_for_many(conn, ids)
    resume_info = {
        job_id: {
            "version": v["version"],
            "pdf_key": v["pdf_key"],
            "changes": diff.summary(list(v["diff"] or [])),
            **(
                skill_additions.boost(list(v["operations"] or []), list(sc["gap"]), float(sc["score"]))
                if (sc := scores.get(job_id)) is not None
                else {}
            ),
        }
        for job_id, v in resumes.items()
    }
    counts: dict[str, int] = dict(
        (await conn.execute(select(cards_table.c.status, func.count()).group_by(cards_table.c.status))).all()
    )
    counts.update(
        dict(
            (
                await conn.execute(
                    select(cards_table.c.outcome, func.count())
                    .where(cards_table.c.outcome.is_not(None))
                    .group_by(cards_table.c.outcome)
                )
            ).all()
        )
    )
    return templates.TemplateResponse(
        request,
        "cards.html",
        page(
            request,
            cards=rows,
            scores=scores,
            sources=sources,
            resumes=resume_info,
            counts=counts,
            status=status,
            filters=CARD_FILTERS,
            metrics=await metrics.strip(conn),
        ),
    )


@router.get("/jobs/all", response_class=HTMLResponse)
async def jobs_page(
    request: Request, conn: Annotated[AsyncConnection, Depends(db)], status: str | None = None
) -> Response:
    """Every job found (pipeline view): status, filter reason, score, sources."""
    if status and status not in {s.value for s in PipelineStatus}:
        status = None
    rows = await jobs_repo.latest(conn, status=status, limit=300)
    ids = [r["id"] for r in rows]
    scores = await scores_repo.latest_for_many(conn, ids)
    sources = await jobs_repo.source_counts(conn, ids)
    counts = await jobs_repo.status_counts(conn)
    hits, shared, recall = await metrics.recall(conn)
    return templates.TemplateResponse(
        request,
        "jobs.html",
        page(
            request,
            jobs=rows,
            scores=scores,
            sources=sources,
            counts=counts,
            status=status,
            statuses=[s.value for s in PipelineStatus],
            recall=recall,
            hits=hits,
            shared=shared,
        ),
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(
    request: Request, job_id: int, conn: Annotated[AsyncConnection, Depends(db)]
) -> Response:
    """Show one job: score, gap and learning plan, resume versions, email and chat.

    An HTMX request gets the drawer fragment instead of the full page.
    """
    job = await jobs_repo.get(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404)
    score = await scores_repo.latest_for(conn, job_id)
    req = await req_repo.get(conn, score["requirements_id"]) if score else None
    card = await cards_repo.for_job(conn, job_id)
    versions = await versions_repo.list_for_job(conn, job_id)
    comparisons = {}
    base_resumes = {}
    # Compare each version with the base resume it was made from.
    for version in versions:
        base_id = version["base_resume_id"]
        if base_id not in base_resumes:
            base_resumes[base_id] = (
                (await conn.execute(select(resume_doc).where(resume_doc.c.id == base_id))).mappings().first()
            )
        baseline = base_resumes[base_id]
        if baseline is not None and req is not None:
            base_doc, wanted = (
                ResumeDoc.model_validate(baseline["doc"]),
                Requirements.model_validate(req["data"]),
            )
            comparisons[version["id"]] = (
                compare_tex(base_doc, dict(version), wanted)
                if version["tex"]
                else compare(base_doc, ResumeDoc.model_validate(version["doc"]), wanted)
            )
    boosts = {
        v["id"]: skill_additions.boost(list(v["operations"] or []), list(score["gap"]), float(score["score"]))
        for v in versions
        if score is not None
    }
    gaps = list(score["gap"]) if score else []
    order = {sid: i for i, sid in enumerate(learning_plan([g.get("skill_id") or "" for g in gaps]))}
    learning = [{**gap, "resources": for_skill(gap.get("skill_id"))} for gap in gaps]
    study_order = sorted(learning, key=lambda g: order.get(g.get("skill_id") or "", len(order)))
    drawer = request.headers.get("HX-Request") == "true"
    return templates.TemplateResponse(
        request,
        "job_drawer.html" if drawer else "job_detail.html",
        page(
            request,
            job=job,
            score=score,
            req=req["data"] if req else None,
            sightings=await jobs_repo.sources_for(conn, job_id),
            card=card,
            resume_versions=versions,
            comparisons=comparisons,
            boosts=boosts,
            base_resumes=base_resumes,
            learning=learning,
            study_order=study_order,
            drawer=drawer,
            outcomes=states.OUTCOMES,
            email=await emails_repo.latest(conn, job_id),
            sent_email=await emails_repo.sent_for_job(conn, job_id),
            chat_messages=await chat_repo.for_job(conn, job_id, limit=100),
            proposals=await proposals_repo.for_job(conn, job_id),
        ),
    )


@router.post("/jobs/{job_id}/email", dependencies=[Depends(auth.require_csrf)])
async def draft_email(
    request: Request,
    job_id: int,
    nonce: Annotated[str, Form(min_length=1)],
    address: Annotated[str, Form()] = "",
) -> Response:
    """Queue the HR email draft for a job (needs an approved resume and Applied)."""
    async with transaction() as conn:
        card = await cards_repo.for_job(conn, job_id)
        if card is None or card["status"] != "applied" or card["approved_resume_version_id"] is None:
            raise HTTPException(status_code=400, detail="Approve the resume and mark this job applied first")
        if await record_action(conn, "email.draft", nonce, {"job_id": job_id}):
            await jobs_queue.enqueue(
                conn,
                kind=EMAIL_DRAFT,
                payload={"job_id": job_id, "address": address.strip(), "nonce": nonce},
                idempotency_key=f"email-draft:{nonce}",
            )
            flash(request, "Email draft queued. Reload shortly for the preview.")
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.post("/jobs/{job_id}/card", dependencies=[Depends(auth.require_csrf)])
async def card_action(
    request: Request,
    job_id: int,
    nonce: Annotated[str, Form(min_length=1)],
    action: Annotated[str, Form()],
    version: Annotated[int | None, Form()] = None,
    outcome: Annotated[str, Form()] = "",
) -> Response:
    """Approve resume vN / I applied / skip / posting closed / outcome - the same transitions as the taps."""
    try:
        async with transaction() as conn:
            card = await cards_repo.for_job(conn, job_id)
            if card is None:
                raise HTTPException(status_code=404)
            event = await record_action(conn, f"card.{action}", nonce, {"job_id": job_id, "version": version})
            if event is not None:
                try:
                    if action == "approve" and version is not None:
                        message = await transitions.approve_resume(conn, card, version, event)
                    elif action == "applied":
                        message = await transitions.applied(conn, card, event)
                    elif action == "skip":
                        message = await transitions.skip(conn, card, event)
                    elif action == "closed":
                        message = await transitions.posting_closed(conn, card, event)
                    elif action == "outcome":
                        message = await transitions.set_outcome(conn, card, outcome, event)
                    else:
                        message = "Unknown action."
                except transitions.Refused as exc:
                    message = str(exc)
                flash(request, message)
    except VersionConflict:
        flash(request, "The card changed meanwhile - here is the current state.")
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.post("/jobs/{job_id}/email/edit", dependencies=[Depends(auth.require_csrf)])
async def edit_email(
    request: Request,
    job_id: int,
    nonce: Annotated[str, Form(min_length=1)],
    subject: Annotated[str, Form(min_length=1, max_length=150)],
    body: Annotated[str, Form(min_length=1)],
    citations: Annotated[str, Form()] = "",
) -> Response:
    """A new email version from your own edit - checked like any draft; it needs a new Send tap."""
    async with transaction() as conn:
        fresh = await record_action(conn, "email.edit", nonce, {"job_id": job_id})
        current = await emails_repo.latest(conn, job_id)
        card = await cards_repo.for_job(conn, job_id)
        job = await jobs_repo.get(conn, job_id)
        facts = fact_texts(await profile_items.list_all(conn, statuses=("confirmed",)))
    if not fresh:
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)
    if current is None or card is None or job is None or card["approved_resume_version_id"] is None:
        flash(request, "Draft the first email after approving the resume.")
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)
    cited = [int(x) for x in citations.replace(",", " ").split() if x.strip().isdigit()]
    errors = email_checks.check(body, cited, facts, subject=subject, job_title=str(job["title"]))
    if errors:
        flash(request, "Not saved: " + "; ".join(errors))
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)
    try:
        edited = EmailDraft(subject=subject, body=body, citations=cited)
        if not current["to_address"]:  # still waiting for an address: keep it waiting (decision 33)
            await email_drafts.store(
                job_id, int(card["approved_resume_version_id"]), "", current["to_source"], edited,
                status=email_drafts.NEEDS_ADDRESS, prompt_version="manual-edit",
            )  # fmt: skip
            flash(request, "Saved. Add the HR address to see it on Telegram.")
            return RedirectResponse(f"/jobs/{job_id}", status_code=303)
        email_id = await email_drafts.create(
            job_id,
            int(card["approved_resume_version_id"]),
            current["to_address"],
            current["to_source"],
            edited,
            prompt_version="manual-edit",
        )
        flash(request, f"Saved as a new version (draft {email_id}); tap Send in Telegram to send it.")
    except ValueError as exc:
        flash(request, str(exc))
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)
