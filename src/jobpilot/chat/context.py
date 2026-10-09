"""Build instruction context while isolating the untrusted job description as data."""

import json

from jobpilot.llm import prompts
from jobpilot.safety import sanitize


def build(*, profile: dict, drafts: dict, thread: list[dict], job: dict, request: str):
    """Render the Job Chat prompt with untrusted text fenced as data.

    The confirmed profile is the only trusted context. Drafts and
    earlier
    messages were written by a model that read the JD, so they are
    fenced as data too - a JD can't reach the instructions second-hand.

    Args:
        profile: Confirmed item id -> fact text.
        drafts: The current resume and email drafts.
        thread: Earlier chat messages for this job.
        job: The job's company, title, JD and score.
        request: The user's message.

    Returns:
        The rendered prompt.
    """
    return prompts.render(
        "agents/job_chat",
        profile=json.dumps(profile, ensure_ascii=True),
        drafts=sanitize.fence(
            "current-drafts", json.dumps(sanitize.clean_value(drafts), ensure_ascii=True, default=str)
        ),
        thread=sanitize.fence(
            "chat-history", json.dumps(sanitize.clean_value(thread), ensure_ascii=True, default=str)
        ),
        job=sanitize.fence(
            "untrusted-job-description", json.dumps(sanitize.clean_value(job), ensure_ascii=True, default=str)
        ),
        request=request,
    )
