"""Job Chat policy: skills not in the profile must be added first."""

from jobpilot.chat.policy import unconfirmed_skill_request


def test_unconfirmed_skill_must_be_added_to_profile_first() -> None:
    """Asking to add a skill the profile lacks is caught; questions are not."""
    facts = {1: "Built APIs with Python and FastAPI."}
    assert unconfirmed_skill_request("add AWS to my resume", facts) == "AWS"
    assert unconfirmed_skill_request("add Python to the first bullet", facts) is None
    assert unconfirmed_skill_request("what does AWS mean in this JD?", facts) is None
