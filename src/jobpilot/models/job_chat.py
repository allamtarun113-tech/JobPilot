"""LLM output schema for one Job Chat turn."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from jobpilot.models.resume_ops import ResumeOp


class JobChatDecision(BaseModel):
    """The tool Job Chat picked for a message, with its arguments.

    Only the fields that belong to ``action`` are used; code applies
    them and re-checks every change.
    """

    # Tier 1 actions answer or draft a new version; tier 2 actions
    # (update_profile, change_prefs, skip_snooze) only make a proposal.
    action: Literal[
        "answer",
        "edit_resume",
        "edit_email",
        "explain_score",
        "prep_help",
        "update_profile",
        "change_prefs",
        "skip_snooze",
        "clarify",
    ]
    reply: str = Field(max_length=1200)
    resume_operations: list[ResumeOp] = Field(default_factory=list)
    email_subject: str | None = None
    email_body: str | None = None
    email_citations: list[int] = Field(default_factory=list)
    proposal: dict[str, Any] = Field(default_factory=dict)
