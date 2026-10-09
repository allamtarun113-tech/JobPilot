"""The study plan built from questions marked weak and the job's gaps."""

from __future__ import annotations

import json
from functools import lru_cache

from jobpilot.core.config import REPO_ROOT


@lru_cache
def catalog() -> dict:
    """Return the project and mock-interview catalog from ``Data/learning``."""
    return json.loads((REPO_ROOT / "Data" / "learning" / "projects_and_checkpoints.json").read_text())


def build(questions: list[dict], gap_skill_ids: list[str]) -> dict:
    """Build the study plan: weak topics, next steps, projects and checkpoints.

    Args:
        questions: The pack's questions with their known/weak marks.
        gap_skill_ids: The job's missing skills.

    Returns:
        The plan as a dict for the Prepare page.
    """
    weak = [question for question in questions if question.get("known") is False]
    weak_topics = list(dict.fromkeys(str(question["topic"]) for question in weak))
    skills = set(gap_skill_ids)
    projects = [p for p in catalog()["portfolio_projects"] if skills & set(p["skill_ids"])][:3]
    checkpoints = [p for p in catalog()["interview_checkpoints"] if skills & set(p["skill_ids"])][:4]
    return {
        "weak_topics": weak_topics,
        "next_actions": [f"Review and retry: {topic}" for topic in weak_topics],
        "projects": projects,
        "checkpoints": checkpoints,
    }
