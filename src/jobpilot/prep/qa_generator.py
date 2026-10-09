"""One model call that writes only the Q&A groups a prep pack is missing.

Decision 34: everything else comes from the Q&A bank.
"""

import json

from jobpilot.llm import prompts
from jobpilot.llm.client import LLMClient, LLMResult
from jobpilot.models.prep import PackQuestions
from jobpilot.safety import sanitize


async def generate_missing(
    llm: LLMClient,
    *,
    wanted: dict[str, list[str]],
    job: dict,
    profile: dict[int, str],
    sources: list[dict],
    research: list[dict],
) -> LLMResult[PackQuestions]:
    """Write Q&A for the prep groups not found in the bank.

    Args:
        llm: The model client.
        wanted: Group -> what it is about, e.g. {"skill": ["PyTorch"],
            "project": ["profile:12"], "hr": [""]}.
        job: The job's company, title and JD.
        profile: Confirmed item id -> fact text.
        sources: Retrieved book sections (the first 20 are sent).
        research: Notes from interview-experience pages.

    Returns:
        The generated questions.
    """
    rows = [
        {"citation": c["citation"], "source": c.get("source"), "page": c.get("page_start"),
         "topic": c.get("topic"), "text": str(c.get("text") or "")[:2000]}
        for c in sources[:20]
    ]  # fmt: skip
    prompt = prompts.render(
        "prep/qa_generator",
        wanted=json.dumps(wanted),
        job=sanitize.fence("job", json.dumps(job, default=str)),
        profile=json.dumps({f"profile:{k}": v for k, v in profile.items()}),
        sources=sanitize.fence("book-sources", json.dumps(rows)),
        research=sanitize.fence("web-research", json.dumps(research)),
    )
    return await llm.structured("qa_generator", prompt, PackQuestions, max_tokens=10000)
