"""Resume v3 tailoring (decision 36): compose -> build -> fit the page -> verify -> review.

Each round the composer (one structured call) picks, orders and lightly rewords the candidate's confirmed
content (resume/pool.py); code applies the plan, refusing any rewording that says more than its one source
fact (resume/compose.py, safety/fidelity.py), and renders the one fixed template - the model never writes
LaTeX. Nothing is cut to fit a page (decision 39): over the target is only noted. The reviewer scores the PDF
text; when it is below the target another round gets its issues and the refused rewordings. Rounds are
capped by `resume.review_max_rounds`, the loop stops once a round doesn't beat the best, and the best verified
round is kept.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from jobpilot.core.config import get_config
from jobpilot.core.errors import LLMInvalidOutput, LLMRefusal
from jobpilot.llm import prompts
from jobpilot.llm.client import LLMClient, LLMResult
from jobpilot.models.requirements import Requirements
from jobpilot.models.resume_doc import ResumeDoc
from jobpilot.models.resume_plan import BulletFixes, ResumePlan
from jobpilot.resume import compose, pool, review, tex_versions, verifier
from jobpilot.resume.render import CompileError, UnsupportedCharacter, render_tex
from jobpilot.safety import claims, sanitize

log = logging.getLogger(__name__)


@dataclass
class Tailored:
    """The best tailored resume: its document, LaTeX, PDF, checks, review and history."""

    tex: str
    pdf: bytes
    verify: dict[str, Any]
    review: dict[str, Any] | None  # the verdict + passed, target, rounds
    applied: list[dict[str, Any]]  # every change from the base resume
    added: list[str]  # kept for callers; v3 never adds unconfirmed skills
    rejected: list[str] = field(default_factory=list)
    rounds: list[dict[str, Any]] = field(default_factory=list)
    prompt_version: str | None = None
    model_id: str | None = None
    doc: ResumeDoc | None = None


async def plan(
    llm: LLMClient,
    candidates: pool.Pool,
    *,
    job: dict[str, object],
    current: str,
    feedback: str,
    run_id: int | None,
) -> LLMResult[ResumePlan]:
    """Ask the composer for this round's plan (not yet checked)."""
    prompt = prompts.render(
        "resume/compose",
        job=sanitize.fence("job", json.dumps(job, ensure_ascii=True, default=str)),
        pool=sanitize.fence("pool", json.dumps(pool.as_prompt(candidates), ensure_ascii=True, indent=1)),
        current=sanitize.fence("current-resume", current) if current else "none",
        feedback=feedback,
    )
    return await llm.structured("resume_composer", prompt, ResumePlan, run_id=run_id, max_tokens=6000)


async def fix_bullets(
    llm: LLMClient, refused: list[dict[str, str]], *, job: dict[str, object], run_id: int | None
) -> BulletFixes | None:
    """Ask once more for the refused bullets, naming each reason. None if the call fails."""
    prompt = prompts.render(
        "resume/fix_bullets",
        job=sanitize.fence("job", json.dumps({"title": job.get("title")}, ensure_ascii=True, default=str)),
        bullets=sanitize.fence("bullets", json.dumps(refused[:20], ensure_ascii=True, indent=1)),
    )
    try:
        result = await llm.structured("resume_composer", prompt, BulletFixes, run_id=run_id, max_tokens=3000)
    except (LLMInvalidOutput, LLMRefusal):
        log.warning("resume bullet second try failed; keeping the first round")
        return None
    return result.output


def with_fixes(planned: ResumePlan, fixes: BulletFixes) -> ResumePlan:
    """The plan with each fixed bullet's new wording in place of the refused one."""
    new = {b.id: b.text for b in fixes.bullets if b.text.strip()}
    plan_copy = planned.model_copy(deep=True)
    for entry in [*plan_copy.projects, *plan_copy.experience]:
        for b in entry.bullets:
            if b.id in new:
                b.text = new[b.id]
    return plan_copy


async def build(
    doc: ResumeDoc, applied: list[dict[str, Any]], *, name: str, max_pages: int
) -> tuple[bytes | None, verifier.VerifyReport]:
    """Build and check the resume. Nothing is cut to fit: more than `max_pages` is only noted."""
    try:
        tex = render_tex(doc)
    except UnsupportedCharacter as exc:  # never lose the card over one character: discard the round
        return None, verifier.VerifyReport(ok=False, max_pages=max_pages, problems=[str(exc)])
    pdf, report = await verifier.verify(tex, applied, name=name, max_pages=99)
    report.max_pages = max_pages
    if pdf is not None and report.pages > max_pages:
        report.notes.append(f"{report.pages} pages (target {max_pages}); nothing was cut")
    return pdf, report


async def tailor(
    llm: LLMClient,
    base_doc: ResumeDoc,
    *,
    job: dict[str, object],
    profile_items: dict[int, str],
    rows: Iterable[Mapping[Any, Any]],
    run_id: int | None,
    gap: list[dict[str, object]] | None = None,
    requirements: Requirements | None = None,
    instruction: str | None = None,
    start_doc: ResumeDoc | None = None,
    max_rounds: int | None = None,
) -> Tailored:
    """Tailor the resume for one job: compose, build, fit, verify, review, repeat.

    Args:
        llm: The model client.
        base_doc: The base resume (layout, header, education, activities).
        job: The job's title, company and JD.
        profile_items: Confirmed item id -> fact text (for the reviewer).
        rows: Confirmed profile item rows (the content the resume is built from).
        run_id: The run the tokens count against.
        gap: The job's gap items (the reviewer must not see them claimed).
        requirements: The job's requirements, for the reviewer.
        instruction: The user's request (a Job Chat edit).
        start_doc: The tailored resume the request applies to.
        max_rounds: Override the configured round limit.

    Returns:
        The best verified resume.

    Raises:
        LLMInvalidOutput: The first composer call failed (the caller falls back to the base resume).
        CompileError: Even the base resume can't be built.
    """
    cfg = get_config().resume
    candidates = pool.build(base_doc, rows)
    lacks = [str(g.get("skill")) for g in gap or []]
    req = requirements
    required = [*req.technologies, *req.skills, *req.concepts, *req.must_skills] if req else []
    nice = list(req.nice_skills) if req else []
    job_text = (
        f"{job.get('title') or ''}\n{job.get('jd') or ''}"  # the title names skills too ("Generative AI")
    )
    claimable = claims.claimable_skills(profile_items)
    name, target = base_doc.header.name, cfg.review_pass_score
    page_limit = max(cfg.max_pages, base_doc.max_pages)
    current = tex_versions.plain(render_tex(start_doc)) if start_doc is not None else ""
    feedback = f"The candidate asked for this change: {instruction}" if instruction else "none"
    best: Tailored | None = None
    rounds: list[dict[str, Any]] = []
    all_rejected: list[str] = []
    prompt_version = model_id = None
    for number in range(1, (max_rounds or cfg.review_max_rounds) + 1):
        try:
            planned = await plan(llm, candidates, job=job, current=current, feedback=feedback, run_id=run_id)
        except (LLMInvalidOutput, LLMRefusal):
            if best is None and number == 1:
                raise
            log.warning("resume composer failed in round %s; keeping the best round", number)
            break
        prompt_version, model_id = getattr(planned, "prompt_id", None), getattr(planned, "model", None)
        built = compose.compose(base_doc, candidates, planned.output, job_text, required=required, nice=nice)
        if built.refused_bullets:  # one focused second try, told exactly what was refused (decision 41)
            fixed = await fix_bullets(llm, built.refused_bullets, job=job, run_id=run_id)
            if fixed is not None:
                retry = compose.compose(
                    base_doc,
                    candidates,
                    with_fixes(planned.output, fixed),
                    job_text,
                    required=required,
                    nice=nice,
                )
                saved = len(built.refused_bullets) - len(retry.refused_bullets)
                if saved > 0:
                    retry.applied.append({"op": "second_try", "section": "all",
                                          "reason": f"second try reworded {saved} more bullets"})  # fmt: skip
                built = retry
        all_rejected += built.rejected
        built.doc.max_pages = page_limit
        applied = built.applied
        pdf, report = await build(built.doc, applied, name=name, max_pages=page_limit)
        record: dict[str, Any] = {"round": number, "applied": applied, "rejected": built.rejected,
                                  "verify": report.as_dict(), "score": None}  # fmt: skip
        rounds.append(record)
        if pdf is None or not report.ok:
            feedback = review.feedback(None, built.rejected, report.problems)
            continue
        try:
            verdict = await review.review(
                llm, await verifier.read_back(pdf), job=job, profile_items=profile_items,
                requirements=requirements, run_id=run_id, skills=claimable, lacks=lacks,
            )  # fmt: skip
        except (LLMInvalidOutput, LLMRefusal) as exc:
            log.warning("resume review unavailable (%s)", type(exc).__name__)
            verdict = None
        record["score"] = verdict.score if verdict else None
        top = best.review["score"] if best is not None and best.review is not None else None
        better = verdict is not None and (top is None or verdict.score > top)
        if best is None or better:
            best = Tailored(
                render_tex(built.doc), pdf, report.as_dict(),
                {**verdict.model_dump(mode="json"), "passed": review.passes(verdict)} if verdict else None,
                applied, [], doc=built.doc,
            )  # fmt: skip
        if verdict is None or review.passes(verdict) or not better:
            break  # passed, unreviewable, or no better than the best round: more rounds only cost calls
        feedback = review.feedback(verdict, built.rejected)
        if instruction:
            feedback = f"The candidate asked for this change: {instruction}\n{feedback}"
    if best is None:  # no round built a resume that passed the verifier: the starting resume, unchanged
        best = await unchanged(start_doc or base_doc)
    if best.review is not None:
        best.review.update(target=target, rounds=len(rounds))
    best.rejected, best.rounds = all_rejected, rounds
    best.prompt_version, best.model_id = prompt_version, model_id
    return best


async def unchanged(base_doc: ResumeDoc, tex: str | None = None) -> Tailored:
    """Build a resume without changes (the base by default). Raises CompileError if it can't be built."""
    tex = tex or tex_versions.base_tex(base_doc)
    page_limit = max(get_config().resume.max_pages, base_doc.max_pages)
    pdf, report = await verifier.verify(tex, [], name=base_doc.header.name, max_pages=page_limit)
    if pdf is None:
        raise CompileError("; ".join(report.problems))
    return Tailored(tex, pdf, report.as_dict(), None, [], [], doc=base_doc)


def describe(applied: list[dict[str, Any]], added: list[str]) -> list[str]:
    """Human-readable changes for the dashboard and the card ('Projects: added project JobPilot')."""
    changes = list(dict.fromkeys(f"{a['section']}: {a.get('reason') or 'reworded'}" for a in applied))
    changes += [f"added skill {s} (Skills)" for s in added]
    return changes or ["No tailoring changes; base resume retained"]
