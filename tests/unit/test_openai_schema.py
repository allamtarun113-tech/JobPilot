"""Every model output must fit the strict provider schema without losing domain validation."""

from typing import Any

import pytest

from jobpilot.llm.schema import strict_schema
from jobpilot.models.email import EmailDraft, EmailPoints
from jobpilot.models.health import PingResult
from jobpilot.models.job_chat import JobChatDecision
from jobpilot.models.prep import PackQuestions, QuestionSet, SkillNote
from jobpilot.models.profile import ExtractedProfile
from jobpilot.models.requirements import Requirements
from jobpilot.models.resume_plan import ResumePlan
from jobpilot.models.resume_review import ResumeReview
from jobpilot.prep.research import Decision as ResearchDecision
from jobpilot.sources.finder_agent import Decision as FinderDecision


@pytest.mark.parametrize(
    "output",
    [
        EmailDraft,
        EmailPoints,
        ResumePlan,
        ResumeReview,
        FinderDecision,
        ResearchDecision,
        PingResult,
        JobChatDecision,
        QuestionSet,
        PackQuestions,
        SkillNote,
        ExtractedProfile,
        Requirements,
    ],
)
def test_output_schemas_are_closed_and_require_all_fields(output: Any) -> None:
    """Every output schema is closed and requires all its fields."""

    def verify(node: Any) -> None:
        """Walk the schema and check every object node."""
        if isinstance(node, list):
            for item in node:
                verify(item)
        elif isinstance(node, dict):
            assert "oneOf" not in node and "discriminator" not in node and "default" not in node
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for child in node.values():
                verify(child)

    verify(strict_schema(output))


def test_chat_proposal_preserves_supported_fields() -> None:
    """The chat proposal schema lists every supported key, each nullable."""
    proposal = strict_schema(JobChatDecision)["properties"]["proposal"]
    assert set(proposal["properties"]) == {"item_id", "text", "field", "value", "action", "kind", "until"}
    assert proposal["properties"]["item_id"]["anyOf"] == [{"type": "integer"}, {"type": "null"}]
