"""Known / Weak marks on prep questions, and the study plan they shape."""

from __future__ import annotations

from jobpilot.db.queries import prep_sets as prep_repo
from jobpilot.db.queries import scores as scores_repo
from jobpilot.db.session import transaction
from jobpilot.prep import study_plan


async def mark(job_id: int, question_id: str, known: bool, expected_version: int) -> None:
    """Mark one prep question known or weak and rebuild the study plan.

    Args:
        job_id: The job whose prep set it is.
        question_id: The question's id.
        known: True for known, False for weak.
        expected_version: The prep set version the page showed.

    Raises:
        ValueError: The set changed meanwhile, or the question is gone.
    """
    async with transaction() as conn:
        prep = await prep_repo.get(conn, job_id)
        score = await scores_repo.latest_for(conn, job_id)
        if prep is None or prep["version"] != expected_version:
            raise ValueError("the preparation set changed; reload the page")
        questions = [dict(question) for question in prep["questions"]]
        found = False
        for question in questions:
            if question["id"] == question_id:
                question["known"], found = known, True
        if not found:
            raise ValueError("question not found")
        gap_ids = [str(gap.get("skill_id") or "") for gap in score["gap"]] if score else []
        await prep_repo.update(
            conn,
            prep["id"],
            prep["version"],
            questions=questions,
            study_plan=study_plan.build(questions, gap_ids),
        )
