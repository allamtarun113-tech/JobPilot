"""The Resume Reviewer: one structured call that scores a built resume against the job, as a recruiter would.

It reads the text of the built PDF (what a recruiter and an ATS see). Its issues go back to the editor each
round; code decides what passes (config resume.review_pass_score, 95 - decision D1). The reviewer never edits
the resume.
"""

from __future__ import annotations

import json
from typing import Any

from jobpilot.core.config import get_config
from jobpilot.llm import prompts
from jobpilot.llm.client import LLMClient
from jobpilot.models.requirements import Requirements
from jobpilot.models.resume_review import ResumeReview
from jobpilot.resume import coverage
from jobpilot.safety import sanitize


def passes(review: ResumeReview) -> bool:
    """Return True if the review score reaches the configured pass score."""
    return review.score >= get_config().resume.review_pass_score


async def review(
    llm: LLMClient,
    resume_text: str,
    *,
    job: dict[str, object],
    profile_items: dict[int, str],
    requirements: Requirements | None,
    run_id: int | None,
    skills: list[str] | None = None,
    lacks: list[str] | None = None,
) -> ResumeReview:
    """Ask the Resume Reviewer to score a built resume for one job.

    Args:
        llm: The model client.
        resume_text: Text read back from the built PDF.
        job: The job's title, company and JD.
        profile_items: Confirmed item id -> fact text.
        requirements: The job's requirements, for the coverage report.
        run_id: The run the tokens count against.
        skills: Skills the resume may claim.
        lacks: Job skills the profile lacks (must not be claimed).

    Returns:
        The verdict, with the score clamped to 0-100.
    """
    report = coverage.report_text(resume_text, requirements) if requirements is not None else {}
    prompt = prompts.render(
        "resume/review",
        profile=sanitize.fence("confirmed-profile", json.dumps(profile_items, ensure_ascii=True)),
        resume=sanitize.fence("resume", resume_text),
        coverage=json.dumps(
            {k: report.get(k) for k in ("covered", "missing", "stuffed")} if report else "unknown"
        ),
        job=sanitize.fence("job", json.dumps(job, ensure_ascii=True, default=str)),
        skills=json.dumps(skills or []),
        lacks=json.dumps(lacks or []),
    )
    result = await llm.structured("resume_review", prompt, ResumeReview, run_id=run_id)
    verdict = result.output
    verdict.score = max(0, min(100, verdict.score))
    return verdict


def feedback(verdict: ResumeReview | None, rejected: list[str], problems: list[str] | None = None) -> str:
    """Format review issues, PDF problems and refused edits for the next round.

    Args:
        verdict: The last review, if any.
        rejected: Edits refused by the checks.
        problems: Problems found in the built PDF.

    Returns:
        The instructions, or "none".
    """
    target = get_config().resume.review_pass_score
    lines = [f"Reviewer score {verdict.score}/100 (target {target})."] if verdict else []
    lines += [f"- {i.target}: {i.problem} Fix: {i.fix}" for i in (verdict.issues if verdict else [])]
    lines += [f"- PDF check: {p}" for p in problems or []]
    lines += [f"- refused, do not repeat: {r}" for r in rejected]
    return "\n".join(lines) or "none"


def summary(verdict: dict[str, Any] | None) -> str:
    """One line for the card caption; says so when the loop ended below the target."""
    if not verdict:
        return "Review: not available"
    state = "ready to send" if verdict.get("passed") else "check before approving"
    line = f"Review: {verdict.get('score')}/100 - {state}"
    if not verdict.get("passed") and verdict.get("target"):
        line += f"; {verdict['target']} not reached after {verdict.get('rounds')} rounds"
        if needs := verdict.get("needs_facts"):
            line += f" - needs from you: {needs[0][:90]}"
    issues = verdict.get("issues") or []
    if issues and not verdict.get("passed"):
        line += f" ({issues[0].get('problem', '')[:90]})"
    return line
