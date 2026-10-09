"""The code-computed match score, the exact gap to 100% and the "why you fit" explanation."""

from __future__ import annotations

from pydantic import BaseModel, Field

# Where a JD item came from (rubric v3); rubric v2 rows used must | nice.
KINDS = ("technology", "skill", "concept", "nice")


class SkillMatch(BaseModel):
    """A JD item the profile covers, with the evidence and how."""

    skill: str  # as the JD names it
    skill_id: str | None
    kind: str  # technology | skill | concept | nice
    evidence: list[int]  # profile item ids that back the match
    how: str  # id | name | experience | similar
    credit: float = 1.0  # share of the item's points earned (below 1: close in meaning, not the same)


class GapItem(BaseModel):
    """A JD item the profile lacks, worth ``points`` toward 100."""

    skill: str
    skill_id: str | None
    kind: str  # technology | skill | concept | nice
    points: float
    # How easy it is to learn (0-1), from the knowledge base: material available + closeness to your skills.
    learnability: float | None = None
    easy: bool | None = None
    learn_reason: str | None = None


class Fit(BaseModel):
    """Why you fit and what's missing, built by code from the score.

    No model call is made. The card, the dashboard and the HR email
    reuse it.
    """

    summary: str = ""
    why: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class ScoreResult(BaseModel):
    """The code-computed score with its parts, matches, gap and reasons."""

    score: float
    parts: dict[str, float]  # dimension -> points earned
    matched: list[SkillMatch]
    gap: list[GapItem]  # ranked by points and learnability: doubles as a learning plan
    reasons: list[str]
    domain_closest: str | None = None  # your project / role text closest to the job's domain
    fit: Fit = Field(default_factory=Fit)
