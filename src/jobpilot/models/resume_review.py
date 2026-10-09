"""The Resume Reviewer's verdict on a tailored resume. Code decides what passes; never LaTeX."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ReviewIssue(BaseModel):
    """One problem the reviewer found and how to fix it."""

    target: str = Field(description="element id (b12, e3, sk) or 'overall'")
    problem: str = Field(max_length=300)
    fix: str = Field(max_length=300, description="what to change, using only confirmed profile facts")


class ResumeReview(BaseModel):
    """The reviewer's score and notes for one tailored resume."""

    score: int = Field(description="0-100: how ready this resume is to send for this job")
    ready: bool
    strengths: list[str] = Field(default_factory=list, max_length=3)
    issues: list[ReviewIssue] = Field(default_factory=list, max_length=8)
    needs_facts: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="what keeps the score down that only NEW confirmed profile facts could fix "
        "(e.g. 'a measurable result for the Turing work'); empty if nothing",
    )
