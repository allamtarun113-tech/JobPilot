"""Schemas for the Prepare page: topics, notes, questions, skill notes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Topic(BaseModel):
    """One topic in the Prep Planner's topic map for a job."""

    name: str = Field(min_length=1, max_length=120)
    category: Literal["technical", "system_design", "behavioral", "company", "dsa"]
    priority: int = Field(ge=1, le=5)
    skill_ids: list[str] = Field(default_factory=list)
    reason: str = Field(max_length=400)


class ResearchNote(BaseModel):
    """A note from a public interview-experience page, with its link."""

    topic: str
    note: str = Field(max_length=1200)
    source_url: str


class Question(BaseModel):
    """One prep question with its answer, citations and source labels."""

    id: str
    category: Literal["technical", "system_design", "behavioral", "company", "dsa"]
    topic: str
    question: str
    answer: str
    citations: list[str] = Field(default_factory=list)
    verified: bool = False
    pattern: str | None = None
    approach: str | None = None
    complexity: str | None = None
    why_asked: str | None = None  # the job requirement or resume line the question comes from
    source_labels: list[str] = Field(
        default_factory=list
    )  # code fills: "Book title, p. 12", "your resume", a URL
    known: bool | None = None
    group: str | None = None  # prep v2: skill | project | job | experience | hr
    from_storage: bool = False  # reused from the Q&A bank (no model call for this pack)


class QuestionSet(BaseModel):
    """The Q&A Generator's list of questions for one job."""

    questions: list[Question] = Field(min_length=1, max_length=40)


class Concept(BaseModel):
    """One key idea inside a skill note, explained simply."""

    name: str = Field(max_length=120)
    explanation: str = Field(max_length=1200)


class NoteQA(BaseModel):
    """A question and answer stored with a skill note."""

    question: str = Field(max_length=500)
    answer: str = Field(max_length=2500)
    citations: list[str] = Field(default_factory=list, max_length=6)


class SkillNote(BaseModel):
    """A deep, simple-English recap of one skill, written once and reused (decision 28)."""

    summary: str = Field(max_length=1500)  # what it is and why it matters, in plain words
    how_it_works: str = Field(max_length=4000)
    key_concepts: list[Concept] = Field(min_length=1, max_length=12)
    when_to_use: str = Field(max_length=1500)
    pitfalls: list[str] = Field(default_factory=list, max_length=8)
    example: str = Field(max_length=2500)
    interview_points: list[str] = Field(default_factory=list, max_length=10)
    qa: list[NoteQA] = Field(default_factory=list, max_length=8)
    citations: list[str] = Field(default_factory=list, max_length=12)  # source ids the note rests on


GROUPS = ("skill", "project", "job", "experience", "hr")


class PackQuestion(BaseModel):
    """One generated Q&A for a prep v2 group (decision 34)."""

    group: Literal["skill", "project", "job", "experience", "hr"]
    about: str = Field(
        default="", max_length=120, description="the skill name, or profile:<id> for a project"
    )
    category: Literal["technical", "system_design", "behavioral", "company", "dsa"]
    question: str = Field(max_length=600)
    answer: str = Field(max_length=3000)
    citations: list[str] = Field(default_factory=list, max_length=6)
    why_asked: str = Field(default="", max_length=300)


class PackQuestions(BaseModel):
    """The questions one AI call writes for the missing prep groups."""

    questions: list[PackQuestion] = Field(default_factory=list, max_length=40)
