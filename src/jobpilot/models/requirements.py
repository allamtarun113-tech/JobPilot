"""LLM output schema for requirement extraction.

The LLM only reads the JD (prompts/scoring/extract_requirements_v2.md);
code computes the score and the gap from these fields (rubric v3,
decision 29). ``must_skills`` is filled by code (technologies + skills)
and never asked of the model, so everything that reads it - tailoring,
prep, cards, evals - keeps working; rows stored by v1 only have
must_skills.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator
from pydantic.json_schema import JsonDict

DERIVED: JsonDict = {"x-derived": True}  # llm/schema.py leaves these fields out of the model's schema


class Seniority(StrEnum):
    """The seniority level a job asks for."""

    INTERN = "intern"
    ENTRY = "entry"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    UNKNOWN = "unknown"


class Requirements(BaseModel):
    """What a job description asks for, as extracted by the LLM."""

    technologies: list[str] = Field(
        default_factory=list,
        description="Required named tools: languages, frameworks, libraries, databases, cloud services, "
        "platforms (e.g. 'Python', 'PyTorch', 'PostgreSQL', 'AWS'). One per entry.",
    )
    skills: list[str] = Field(
        default_factory=list,
        description="Required abilities and methods that are not a named tool (e.g. 'machine learning', "
        "'data analysis', 'API design', 'stakeholder communication'). One per entry.",
    )
    concepts: list[str] = Field(
        default_factory=list,
        description="Knowledge areas and ideas the JD expects you to understand (e.g. 'transformers', "
        "'distributed systems', 'object-oriented design', 'RAG'). One per entry.",
    )
    project_types: list[str] = Field(
        default_factory=list,
        description="Kinds of things you would build or have built (e.g. 'recommendation system', "
        "'LLM chatbot', 'ETL pipeline', 'BI dashboards').",
    )
    nice_skills: list[str] = Field(
        description="Anything listed as preferred / a plus / nice to have (tools, skills or concepts)."
    )
    years_min: float | None = Field(description="Minimum years of experience asked, or null if not stated.")
    years_max: float | None = Field(description="Maximum years of experience, or null if not stated.")
    seniority: Seniority
    domain: str = Field(
        description="Business domain and product type in a few words, e.g. 'fintech payments API'."
    )
    title_norm: str = Field(description="The role title without company, location or seniority noise.")
    is_job_description: bool = Field(description="False if the text is not a job description at all.")
    must_skills: list[str] = Field(default_factory=list, json_schema_extra=DERIVED)

    @model_validator(mode="after")
    def _derive_must_skills(self) -> Requirements:
        """Fill ``must_skills`` from technologies and skills when empty.

        Returns:
            The model itself, as pydantic's after-validator expects.
        """
        # Rows stored by v1 already carry must_skills; keep them.
        if not self.must_skills:
            self.must_skills = list(dict.fromkeys([*self.technologies, *self.skills]))
        return self
