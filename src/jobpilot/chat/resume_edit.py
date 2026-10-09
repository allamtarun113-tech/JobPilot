"""Tier-1 Job Chat resume edits and undo.

Each edit makes a new resume version (the old one is kept) and resets
the card's approval, then sends a preview with Approve vN / Change
more / Undo.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from uuid import uuid4

from jobpilot.cards import states
from jobpilot.db import leases
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.queries import profile_items, requirements, resume_docs
from jobpilot.db.queries import resume_versions as versions_repo
from jobpilot.db.queries import scores as scores_repo
from jobpilot.db.session import transaction
from jobpilot.db.tables import jobs as jobs_table
from jobpilot.integrations.storage import get_store
from jobpilot.llm.client import LLMClient
from jobpilot.models.profile import fact_texts
from jobpilot.models.requirements import Requirements
from jobpilot.models.resume_doc import ResumeDoc
from jobpilot.models.resume_ops import ResumeOp
from jobpilot.queue import outbox
from jobpilot.resume import (
    coverage,
    diff,
    ops,
    parse_check,
    preview,
    render,
    skill_additions,
    tailor,
    tex_versions,
)
from jobpilot.safety import idempotency

LEASE_SECONDS = 300
log = logging.getLogger(__name__)


async def _with_job_lease[R](job_id: int, work: Callable[[], Awaitable[R]]) -> R:
    """Hold the job's short row lease (never a DB lock) so a run can't tailor the same job meanwhile."""
    holder = f"chat-{uuid4().hex[:12]}"
    async with transaction() as conn:
        if not await leases.take(conn, jobs_table, job_id, holder, LEASE_SECONDS):
            raise ValueError("This job is being updated by the current run; try again in a minute.")
    try:
        return await work()
    finally:
        async with transaction() as conn:
            await leases.release(conn, jobs_table, job_id, holder)


async def edit(
    job_id: int, operations: list[ResumeOp], *, llm: LLMClient | None = None, instruction: str | None = None
) -> int:
    """Make a new resume version from a Job Chat request.

    Args:
        job_id: The job.
        operations: Structured edits (for pre-v2 resumes).
        llm: The model client (needed for v2 LaTeX copies).
        instruction: The user's request (for v2 LaTeX copies).

    Returns:
        The new version number.

    Raises:
        ValueError: The edit was refused or the resume isn't editable.
    """
    return await _with_job_lease(job_id, lambda: _edit(job_id, operations, llm, instruction))


async def _edit(
    job_id: int, operations: list[ResumeOp], llm: LLMClient | None, instruction: str | None
) -> int:
    """Apply a chat edit while holding the job's lease."""
    async with transaction() as conn:
        current = await versions_repo.latest(conn, job_id)
        card = await cards_repo.for_job(conn, job_id)
        job = await jobs_repo.get(conn, job_id)
        score = await scores_repo.latest_for(conn, job_id)
        req = await requirements.get(conn, score["requirements_id"]) if score else None
        confirmed = await profile_items.list_all(conn, statuses=("confirmed",))
    if current is None or card is None or job is None or req is None:
        raise ValueError("this job has no editable resume draft")
    _editable(card)
    facts = fact_texts(confirmed)
    before = ResumeDoc.model_validate(current["doc"])
    requirement = Requirements.model_validate(req["data"])
    if current["tex"]:  # resume v2/v3: the composer applies your request, with the same checks
        if llm is None or not instruction:
            raise ValueError("editing this resume needs the model and your request")
        async with transaction() as conn:
            base_row = await resume_docs.current(conn)
        if base_row is None:
            raise ValueError("there is no base resume to build from")
        base = ResumeDoc.model_validate(base_row["doc"])
        result = await tailor.tailor(
            llm,
            base,
            job={"title": job["title"], "company": job["company"], "jd": job["jd_text"]},
            profile_items=facts,
            rows=confirmed,
            run_id=None,
            gap=list(score["gap"]) if score else [],
            requirements=requirement,
            instruction=instruction,
            start_doc=before if before.composed else None,
            max_rounds=1,
        )
        if result.doc is None or result.tex == current["tex"]:
            reasons = "; ".join(result.rejected) or "the composer proposed no change"
            raise ValueError("resume edit rejected: " + reasons)
        return await _persist(
            job_id, current, card, job,
            doc=result.doc, pdf=result.pdf, report={**result.verify, "review": result.review},
            keyword_coverage=coverage.report_text(tex_versions.plain(result.tex), requirement),
            changes=tailor.describe(result.applied, result.added),
            operations=[*result.applied, *({"op": "add_skill", "skill": s} for s in result.added)],
            tex=result.tex, rounds=result.rounds,
        )  # fmt: skip
    addable = tuple(
        str(a["skill"]) for a in skill_additions.eligible(list(score["gap"]) if score else [], before)
    )
    changed, applied, rejected = ops.apply(before, operations, facts, addable)
    if rejected:
        raise ValueError("resume edit rejected: " + "; ".join(rejected))
    if not applied:
        raise ValueError("the edit did not produce a resume change")
    return await _persist_doc(job_id, current, card, job, requirement, before, changed, applied)


async def undo(job_id: int, version_to_undo: int) -> int:
    """Restore the version before ``version_to_undo`` as a new version.

    Raises:
        ValueError: The version isn't the latest, or there's nothing
            earlier.
    """
    return await _with_job_lease(job_id, lambda: _undo(job_id, version_to_undo))


async def _undo(job_id: int, version_to_undo: int) -> int:
    """Restore the previous version while holding the job's lease."""
    async with transaction() as conn:
        versions = await versions_repo.list_for_job(conn, job_id)
        card = await cards_repo.for_job(conn, job_id)
        job = await jobs_repo.get(conn, job_id)
        score = await scores_repo.latest_for(conn, job_id)
        req = await requirements.get(conn, score["requirements_id"]) if score else None
    if not versions or versions[0]["version"] != version_to_undo:
        raise ValueError("that resume is stale; only the latest version can be undone")
    if len(versions) < 2 or card is None or job is None or req is None:
        raise ValueError("there is no earlier resume version to restore")
    _editable(card)
    current, previous = versions[0], versions[1]
    requirement = Requirements.model_validate(req["data"])
    undo_op = [{"op": "undo", "restored_version": previous["version"]}]
    if previous["tex"]:  # restore the earlier LaTeX copy as a new version
        base = ResumeDoc.model_validate(previous["doc"])
        rebuilt = await tailor.unchanged(base, previous["tex"])
        return await _persist(
            job_id, current, card, job,
            doc=base, pdf=rebuilt.pdf, report=rebuilt.verify,
            keyword_coverage=coverage.report_text(tex_versions.plain(previous["tex"]), requirement),
            changes=[f"restored v{previous['version']}"], operations=undo_op,
            tex=previous["tex"], rounds=None,
        )  # fmt: skip
    return await _persist_doc(
        job_id,
        current,
        card,
        job,
        requirement,
        ResumeDoc.model_validate(current["doc"]),
        ResumeDoc.model_validate(previous["doc"]),
        undo_op,
    )


async def _persist_doc(job_id, current, card, job, requirement, before, changed, applied) -> int:
    """A version built from the structured resume (versions made before resume v2)."""
    pdf = await render.render_pdf(changed)
    report = (await parse_check.acheck(changed, pdf)).as_dict()
    if not report["ok"]:
        raise ValueError("edited resume failed the parse check")
    return await _persist(
        job_id, current, card, job,
        doc=changed, pdf=pdf, report=report, keyword_coverage=coverage.report(changed, requirement),
        changes=diff.describe(before, changed), operations=applied, tex=None, rounds=None,
    )  # fmt: skip


async def _persist(
    job_id, current, card, job, *, doc, pdf, report, keyword_coverage, changes, operations, tex, rounds
) -> int:
    """Store a new chat-made version and send its preview, version-checked.

    Raises:
        ValueError: The PDF check failed, or the resume or card changed
            while the edit was being made.
    """
    if not report.get("ok"):
        raise ValueError("the edited resume failed the PDF check: " + "; ".join(report.get("problems") or []))
    key = f"resumes/jobs/{job_id}/{uuid4().hex}.pdf"
    await get_store().put(key, pdf)
    try:  # page 1 as an image for the preview; optional
        await get_store().put(preview.key_for(key), await preview.render(pdf))
    except Exception:
        log.warning("no resume preview for %s", key, exc_info=True)
    # Re-check that nothing changed while the model was working.
    async with transaction() as conn:
        version = await versions_repo.next_version(conn, job_id)
        latest = await versions_repo.latest(conn, job_id)
        fresh_card = await cards_repo.for_job(conn, job_id)
        if (
            latest is None
            or latest["id"] != current["id"]
            or fresh_card is None
            or fresh_card["version"] != card["version"]
        ):
            raise ValueError("the resume or card changed; retry the edit")
        _editable(fresh_card)
        states.require(fresh_card["status"], "new")
        version_id = await versions_repo.create(
            conn,
            job_id=job_id,
            base_resume_id=current["base_resume_id"],
            version=version,
            doc=doc.model_dump(mode="json"),
            operations=operations,
            pdf_key=key,
            parse_ok=True,
            parse_report=report,
            keyword_coverage=keyword_coverage,
            diff=changes,
            created_by="chat",
            tex=tex,
            rounds=rounds,
        )
        await cards_repo.update(
            conn, card["id"], card["version"], status="new", approved_resume_version_id=None
        )
        await outbox.enqueue(
            conn,
            partition_key=outbox.card_partition(card["id"]),
            kind="telegram",
            payload={
                "op": "send_photo",
                "storage_key": preview.key_for(key),
                "caption": f"Resume v{version} - page 1",
            },
            idempotency_key=idempotency.outbox("chat-resume-preview", version_id),
        )
        await outbox.enqueue(
            conn,
            partition_key=outbox.card_partition(card["id"]),
            kind="telegram",
            payload={
                "op": "send_document",
                "storage_key": key,
                "caption": f"Resume preview v{version} - {job['company']} / {job['title']}",
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {
                                "text": f"Approve v{version}",
                                "callback_data": f"ra:{card['callback_token']}:{version}",
                            },
                            {"text": "Change more", "callback_data": f"rc:{card['callback_token']}"},
                            {
                                "text": "Undo",
                                "callback_data": f"ru:{card['callback_token']}:{version}",
                            },
                        ]
                    ]
                },
            },
            idempotency_key=idempotency.outbox("chat-resume", version_id),
        )
    return version


def _editable(card) -> None:
    """Raise ValueError unless the card still allows resume edits."""
    if card["status"] not in states.NO_ACTION_STATES:
        raise ValueError("resume edits are unavailable after application or while sending")
