"""Structured resume operations returned by the model; never LaTeX."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class RewriteBullet(BaseModel):
    """Reword one bullet; the new text must cite profile item ids."""

    op: Literal["rewrite_bullet"]
    bullet_id: str
    text: str = Field(min_length=1, max_length=500)
    evidence: list[int] = Field(min_length=1)


class HideBullet(BaseModel):
    """Hide one bullet from this job's resume."""

    op: Literal["hide_bullet"]
    bullet_id: str


class MoveEntry(BaseModel):
    """Move an entry before another one (or to the end when None)."""

    op: Literal["move_entry"]
    entry_id: str
    before_entry_id: str | None = None


class RewriteSummary(BaseModel):
    """Rewrite (e.g. shorten) a paragraph entry such as the career summary; cited like a bullet."""

    op: Literal["rewrite_summary"]
    entry_id: str
    text: str = Field(min_length=1, max_length=900)
    evidence: list[int] = Field(min_length=1)


class ReorderSkills(BaseModel):
    """Reorder the items of one skills line. The same items only - nothing added or removed."""

    op: Literal["reorder_skills"]
    entry_id: str
    items: list[str] = Field(min_length=1, max_length=60)


class AddSkill(BaseModel):
    """Add one missing job skill to a skills line. Only skills code marked as addable are accepted."""

    op: Literal["add_skill"]
    entry_id: str
    skill: str = Field(min_length=1, max_length=60)


ResumeOp = Annotated[
    RewriteBullet | HideBullet | MoveEntry | RewriteSummary | ReorderSkills | AddSkill,
    Field(discriminator="op"),
]
