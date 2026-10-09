"""One job posting as every source reports it, before de-duplication."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class SourceKind(StrEnum):
    """Which kind of source reported a job."""

    API = "api"  # jobs API (JSearch)
    SEARCH = "search"  # Job Finder agent leads
    SHARED = "shared"  # shared to the bot by the user


class PipelineStatus(StrEnum):
    """Where a job is in the find, filter and score pipeline."""

    NEW = "new"
    FILTERED = "filtered"
    SCREENED_OUT = "screened_out"  # old runs only: stage A no longer cuts jobs (decision 27)
    PENDING_EXTRACTION = "pending_extraction"  # waiting for the LLM (e.g. no API key yet)
    SCORED = "scored"
    QUEUED = "queued"
    BELOW_BAR = "below_bar"
    CARDED = "carded"
    EXPIRED = "expired"  # waited in the queue for 10 days without being carded


class Lead(BaseModel):
    """A job lead from web-search results (Job Finder): title, company, link, snippet - nothing scraped."""

    title: str = Field(min_length=1, max_length=200)
    company: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=8, max_length=4000)
    snippet: str = Field(default="", max_length=4000)
    location: str | None = Field(default=None, max_length=200)


class JobPosting(BaseModel):
    """A sighting of a job from one source. `external_id` is unique per source."""

    source: SourceKind
    external_id: str
    title: str
    company: str
    location: str | None = None
    url: str
    board: str | None = None  # board the job id belongs to (linkedin, naukri, indeed, ...)
    board_job_id: str | None = None
    description: str = ""
    description_is_snippet: bool = True
    posted_at: datetime | None = None
    remote: bool | None = None
    experience_text: str | None = None
    raw: dict[str, object] = Field(default_factory=dict)
