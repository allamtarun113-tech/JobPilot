"""The resume composer's output (resume v3, decision 36): a plan code applies, never LaTeX or free text.

The model picks and orders entries and bullets by id and may reword one bullet at a time. Code builds the
resume from the confirmed profile and checks every reworded line against its one source fact
(resume/compose.py, safety/fidelity.py).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PlannedBullet(BaseModel):
    """One bullet, by id, with optional new wording of that same fact."""

    id: str = Field(max_length=20, description="the bullet id from the pool, e.g. 'b4' or 'p47'")
    text: str = Field(
        max_length=400,
        description="the same fact reworded for this job, or '' to keep the original wording",
    )


class PlannedEntry(BaseModel):
    """One experience or project, by id, with its bullets in the order to show."""

    id: str = Field(max_length=20, description="the entry id from the pool, e.g. 'e15' or 'p69'")
    bullets: list[PlannedBullet] = Field(max_length=10)


class PlannedSkillLine(BaseModel):
    """One skills line: a label and the skills on it, most relevant first."""

    label: str = Field(max_length=40)
    items: list[str] = Field(max_length=16)


class ResumePlan(BaseModel):
    """Everything the composer decides for one job."""

    summary: str = Field(
        max_length=600, description="2-3 sentence career objective for this job, or '' to keep the original"
    )
    projects: list[PlannedEntry] = Field(
        max_length=8, description="every project, most relevant to the job first (none is ever left out)"
    )
    experience: list[PlannedEntry] = Field(max_length=8, description="every experience, bullets ordered")
    skills: list[PlannedSkillLine] = Field(
        max_length=10, description="skills lines with only the skills this job asks for, most relevant first"
    )


class BulletFixes(BaseModel):
    """The second try for bullets whose first rewording was refused (decision 41)."""

    bullets: list[PlannedBullet] = Field(max_length=20, description="one per refused bullet, by its id")
