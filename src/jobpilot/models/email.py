"""LLM output schema for the HR email writer."""

from pydantic import BaseModel, Field


class EmailDraft(BaseModel):
    """An HR email draft whose claims cite profile item ids."""

    subject: str = Field(max_length=150)
    body: str
    citations: list[int]


class FitPoint(BaseModel):
    """One sentence on why the applicant fits, resting on exactly one profile fact."""

    fact_id: int = Field(description="the id of the one experience or project fact this sentence states")
    sentence: str = Field(max_length=300, description="that fact in one first-person sentence")


class EmailPoints(BaseModel):
    """The only part of the HR email a model writes (decision 36): code writes the rest."""

    points: list[FitPoint] = Field(max_length=3, description="2-3 facts most relevant to the job, best first")
