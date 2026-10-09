"""Build one actionable card and tailored resume, with side effects committed through the outbox.

Steps: reserve daily capacity -> take the job's row lease -> tailor this job's copy of the resume LaTeX
(edit -> build -> verify -> review rounds, outside any transaction; resume/tailor.py) -> store the PDF under a
unique key -> one short transaction that writes the version (with its .tex and every round), the card and its
outbox rows. If the editor can't run, the unchanged base copy is used instead of failing the card.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from jobpilot.cards import states
from jobpilot.cards.messages import edit_card, send_card, send_resume
from jobpilot.core.errors import LLMInvalidOutput, LLMRefusal
from jobpilot.core.ids import short_id
from jobpilot.db import leases
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.queries import preferences as prefs_repo
from jobpilot.db.queries import profile_items, requirements, resume_docs
from jobpilot.db.queries import resume_versions as versions_repo
from jobpilot.db.queries import scores as scores_repo
from jobpilot.db.session import transaction
from jobpilot.db.tables import jobs as jobs_table
from jobpilot.integrations.storage import get_store
from jobpilot.llm.client import LLMClient
from jobpilot.models.card import CardStatus
from jobpilot.models.job import PipelineStatus
from jobpilot.models.profile import fact_texts
from jobpilot.models.requirements import Requirements
from jobpilot.models.resume_doc import ResumeDoc
from jobpilot.models.work import EMAIL_DRAFT
from jobpilot.queue import jobs_queue
from jobpilot.resume import coverage, preview, tailor, tex_versions
from jobpilot.safety import caps

log = logging.getLogger(__name__)
# How long one build may hold the job's row lease.
LEASE_SECONDS = 600
# A card can only be rebuilt before the user has applied.
REBUILDABLE = {CardStatus.NEW.value, CardStatus.RESUME_APPROVED.value}


class JobBusy(ValueError):
    """Another step (a run, a Job Chat edit) holds this job's row lease right now."""


async def build(job_id: int, llm: LLMClient, *, run_id: int | None, note: str | None = None) -> int:
    """Build (or rebuild) one job's card and tailored resume.

    Holds a daily-capacity reservation (renewed in the background) and
    the job's row lease for the whole build; both are always released.

    Args:
        job_id: The job to card.
        llm: The model client.
        run_id: The run the tokens count against.
        note: The note shown on a rebuilt card.

    Returns:
        The card id.

    Raises:
        CapReached: Today's card limit is reached.
        JobBusy: Another step holds this job's lease.
    """
    async with transaction() as conn:
        token = await caps.reserve_card(conn, job_id)
    holder = f"build-{token[:12]}"
    heartbeat = asyncio.create_task(caps.renew_card(job_id, token))
    try:
        async with transaction() as conn:
            if not await leases.take(conn, jobs_table, job_id, holder, LEASE_SECONDS):
                raise JobBusy("this job is being changed by another step; try again shortly")
        try:
            return await _build(job_id, llm, run_id=run_id, reservation=token, note=note)
        finally:
            async with transaction() as conn:
                await leases.release(conn, jobs_table, job_id, holder)
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
        async with transaction() as conn:
            await caps.release_card(conn, job_id, token)


async def _tailored(
    llm: LLMClient,
    base_doc: ResumeDoc,
    job: Any,
    facts: dict[int, str],
    rows: list[Any],
    run_id: int | None,
    gap: list[dict[str, Any]],
    requirement: Requirements,
) -> tailor.Tailored:
    """This job's tailored resume (resume v3), or the unchanged base resume when the composer can't run."""
    try:
        return await tailor.tailor(
            llm,
            base_doc,
            job={"title": job["title"], "company": job["company"], "jd": job["jd_text"]},
            profile_items=facts,
            rows=rows,
            run_id=run_id,
            gap=gap,
            requirements=requirement,
        )
    except (LLMInvalidOutput, LLMRefusal) as exc:
        log.warning("tailoring failed for job %s (%s); using the base resume", job["id"], exc)
        result = await tailor.unchanged(base_doc)
        result.rejected.append(f"tailoring unavailable: {type(exc).__name__}")
        return result


async def _store_preview(pdf_key: str, pdf: bytes) -> None:
    """Page 1 as a PNG next to the PDF, for the card. Optional: without it the card just sends the PDF."""
    try:
        await get_store().put(preview.key_for(pdf_key), await preview.render(pdf))
    except Exception:
        log.warning("no resume preview for %s", pdf_key, exc_info=True)


async def _build(
    job_id: int, llm: LLMClient, *, run_id: int | None, reservation: str, note: str | None
) -> int:
    """Tailor, store the PDF, then save the version, card and messages.

    The model work happens with no transaction open; one short
    transaction then re-checks the reservation and writes everything.

    Raises:
        ValueError: The job isn't scored yet, or its card can no longer
            be rebuilt.
    """
    async with transaction() as conn:
        existing = await cards_repo.for_job(conn, job_id)
        job = await jobs_repo.get(conn, job_id)
        score = await scores_repo.latest_for(conn, job_id)
        base = await resume_docs.current(conn)
        confirmed = await profile_items.list_all(conn, statuses=("confirmed",))
        req_row = await requirements.get(conn, score["requirements_id"]) if score else None
    if job is None or score is None or base is None or req_row is None:
        raise ValueError(f"job {job_id} is not ready for a card")
    if existing is not None and existing["status"] not in REBUILDABLE:
        raise ValueError("this card cannot be rebuilt after application or while sending")
    base_doc = ResumeDoc.model_validate(base["doc"])
    facts = fact_texts(confirmed)
    requirement = Requirements.model_validate(req_row["data"])
    result = await _tailored(llm, base_doc, job, facts, confirmed, run_id, list(score["gap"]), requirement)
    operations = [*result.applied, *({"op": "add_skill", "skill": s} for s in result.added)]
    # The reservation token makes the key unique per build, so a
    # retried build never overwrites a PDF a version already points to.
    key = f"resumes/jobs/{job_id}/{reservation}.pdf"
    await get_store().put(key, result.pdf)
    await _store_preview(key, result.pdf)
    async with transaction() as conn:
        await caps.check_card_reservation(conn, job_id, reservation)
        version = await versions_repo.next_version(conn, job_id)
        version_id = await versions_repo.create(
            conn,
            job_id=job_id,
            base_resume_id=base["id"],
            version=version,
            doc=(result.doc or base_doc).model_dump(mode="json"),
            operations=operations,
            pdf_key=key,
            parse_ok=bool(result.verify.get("ok")),
            parse_report={**result.verify, "review": result.review, "rejected_operations": result.rejected},
            keyword_coverage=coverage.report_text(tex_versions.plain(result.tex), requirement),
            diff=tailor.describe(result.applied, result.added),
            created_by="tailor",
            prompt_version=result.prompt_version,
            model_id=result.model_id,
            tex=result.tex,
            rounds=result.rounds,
        )
        if existing is None:
            card_id = await cards_repo.create(conn, job_id=job_id, callback_token=short_id())
            prefs_row = await prefs_repo.get(conn)
            digest = bool(prefs_row and prefs_row["data"].get("digest_mode"))
            if not digest:  # in digest mode the card is sent when you open it from the daily list
                await send_card(conn, card_id)
        else:
            card_id = int(existing["id"])
            # A rebuild resets approval: the new version needs a new
            # tap.
            states.require(existing["status"], CardStatus.NEW.value)
            await cards_repo.update(
                conn,
                card_id,
                existing["version"],
                status=CardStatus.NEW.value,
                approved_resume_version_id=None,
            )
            if existing["telegram_outbox_id"] is not None:
                await edit_card(
                    conn,
                    card_id,
                    note or f"Resume v{version} drafted; approve it again.",
                    f"rebuild:{version}",
                )
                await send_resume(conn, card_id, job, key, version, version_id)
        current_job = await jobs_repo.get(conn, job_id)
        assert current_job is not None
        await jobs_repo.update(
            conn, job_id, current_job["version"], pipeline_status=PipelineStatus.CARDED.value
        )
        await caps.release_card(conn, job_id, reservation)
        # The HR email draft for this card, written in the background and kept until you tap Email HR (D3).
        await jobs_queue.enqueue(
            conn,
            kind=EMAIL_DRAFT,
            payload={"job_id": job_id, "auto": True},
            idempotency_key=f"email-auto:{job_id}:{version_id}",
        )
    return card_id
