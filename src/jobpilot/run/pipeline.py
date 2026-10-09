"""The per-job pipeline, after the batch steps (dedup, hard filters, stage A):

full-JD lookup (cheap first, agent only if that fails) -> requirement extraction (LLM, cached)
-> code-computed score (rubric v3) -> learnability + "why you fit" -> threshold -> queue.

Each step is read -> compute (no transaction, no lock) -> short write. A short row lease on the job keeps a
run and a shared-link handler from working on the same job at once.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace

from jobpilot.core import clock
from jobpilot.db import leases
from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.queries import queue as queue_repo
from jobpilot.db.queries import scores as scores_repo
from jobpilot.db.session import transaction
from jobpilot.db.tables import jobs as jobs_table
from jobpilot.matching import extract, gap
from jobpilot.matching.compute import SkillMatcher, compute, explain, rubric
from jobpilot.matching.profile_view import ProfileView
from jobpilot.models.job import PipelineStatus
from jobpilot.models.preferences import Preferences
from jobpilot.reference import normalize
from jobpilot.run.deps import RunDeps
from jobpilot.safety import sanitize
from jobpilot.sources import queue_rank
from jobpilot.sources.jd_enrich import find_full_jd

log = logging.getLogger(__name__)
LEASE_SECONDS = 300


@dataclass
class JobContext:
    """What processing one job needs: deps, preferences, profile and lease holder."""

    deps: RunDeps
    prefs: Preferences
    view: ProfileView
    matcher: SkillMatcher
    holder: str
    run_id: int | None


async def _update(job_id: int, **values: Any) -> None:
    """Version-checked update of one job row, in its own transaction."""
    async with transaction() as conn:
        row = await jobs_repo.get(conn, job_id)
        assert row is not None
        await jobs_repo.update(conn, job_id, row["version"], **values)


async def _load(job_id: int) -> dict[str, Any]:
    """Load one job row as a dict."""
    async with transaction() as conn:
        row = await jobs_repo.get(conn, job_id)
    assert row is not None
    return dict(row)


async def process(job_id: int, ctx: JobContext) -> str:
    """Run one job through the pipeline and return its final status ('locked' if another holder has it)."""
    async with transaction() as conn:
        if not await leases.take(conn, jobs_table, job_id, ctx.holder, LEASE_SECONDS):
            return "locked"
    try:
        return await _process(job_id, ctx)
    finally:
        async with transaction() as conn:
            await leases.release(conn, jobs_table, job_id, ctx.holder)


async def _process(job_id: int, ctx: JobContext) -> str:
    """Run one job through lookup, extraction, scoring and the threshold.

    Returns:
        The job's new pipeline status.
    """
    job = await _load(job_id)

    # 1. Full JD for every snippet job: free careers-site lookups first, the agent if they fail. A job
    # whose JD can't be found is marked "not_found" and scored on its snippet (the card says so).
    if job["jd_is_snippet"] and job["jd_lookup"] != "not_found":
        found = await find_full_jd(job, ctx.deps.enrich, ctx.deps.agent, ctx.run_id)
        if found:
            text, where = found
            await _update(
                job_id,
                jd_text=text,
                jd_is_snippet=False,
                jd_hash=normalize.jd_hash(text),
                jd_simhash=normalize.simhash(text),
                jd_lookup=f"found:{where}"[:500],
            )
        else:
            await _update(job_id, jd_lookup="not_found")
        job = await _load(job_id)

    if sanitize.suspicious(job["jd_text"] or ""):
        trace.get_current_span().set_attribute("job.jd_suspicious", True)
        log.warning("job %s: JD contains instruction-like text (it is stripped and fenced as data)", job_id)

    # 2. Requirements (one LLM call per JD, cached).
    if ctx.deps.llm is None:
        await _update(
            job_id,
            pipeline_status=PipelineStatus.PENDING_EXTRACTION.value,
            filter_reason="waiting for OPENAI_API_KEY",
        )
        return PipelineStatus.PENDING_EXTRACTION.value
    jd_hash = job["jd_hash"] or normalize.jd_hash(job["jd_text"] or job["title"])
    req_id, req = await extract.extract(
        ctx.deps.llm,
        jd_text=job["jd_text"] or job["title"],
        jd_hash=jd_hash,
        title=job["title"],
        run_id=ctx.run_id,
    )
    if not req.is_job_description:
        await _update(
            job_id, pipeline_status=PipelineStatus.FILTERED.value, filter_reason="not a job description"
        )
        return PipelineStatus.FILTERED.value

    # 3. Score + gap, computed by code.
    result = await compute(
        req,
        ctx.view,
        embedder=ctx.deps.embedder,
        vectors=ctx.deps.vectors,
        matcher=ctx.matcher,
        jd_is_snippet=bool(job["jd_is_snippet"]),
    )
    result.gap = await gap.with_learnability(
        result.gap, set(ctx.view.skill_ids), ctx.deps.embedder, ctx.deps.vectors
    )
    result.fit = explain(result, ctx.view)  # again: "missing" now follows the learnability ranking

    # 4. Threshold -> queue.
    status = PipelineStatus.QUEUED if result.score >= ctx.prefs.threshold else PipelineStatus.BELOW_BAR
    async with transaction() as conn:
        await scores_repo.add(
            conn,
            job_id=job_id,
            requirements_id=req_id,
            stage_a=job["stage_a"],
            score=result.score,
            parts=result.parts,
            matched=[m.model_dump() for m in result.matched],
            gap=[g.model_dump() for g in result.gap],
            fit=result.fit.model_dump(),
            rubric_version=int(rubric()["rubric_version"]),
            profile_hash=ctx.view.hash,
        )
        if status == PipelineStatus.QUEUED:
            await queue_rank.enqueue(conn, job_id, result.score, clock.now())
        else:
            await queue_repo.remove(conn, job_id)
        row = await jobs_repo.get(conn, job_id)
        assert row is not None
        reason = (
            None if status == PipelineStatus.QUEUED else f"score {result.score:g} below {ctx.prefs.threshold}"
        )
        await jobs_repo.update(
            conn, job_id, row["version"], pipeline_status=status.value, filter_reason=reason
        )
    return status.value
