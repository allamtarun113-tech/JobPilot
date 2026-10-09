"""One LLM call per JD -> Requirements, cached by JD hash + prompt version + model id."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core.config import get_config
from jobpilot.db.queries import requirements as repo
from jobpilot.db.session import transaction
from jobpilot.llm import prompts
from jobpilot.llm.client import LLMClient
from jobpilot.models.requirements import Requirements
from jobpilot.safety.sanitize import fence

TASK = "extract_requirements"
PROMPT = "scoring/extract_requirements"


def cache_key() -> tuple[str, str]:
    """Return the (prompt version, model id) part of the extraction cache key."""
    cfg = get_config()
    return f"{PROMPT}_v{cfg.prompt_versions[PROMPT]}", cfg.model_for(TASK)


async def cached(conn: AsyncConnection, jd_hash: str) -> tuple[int, Requirements] | None:
    """Return a cached (requirements id, Requirements) for this JD, or None."""
    prompt_version, model = cache_key()
    row = await repo.cached(conn, jd_hash, prompt_version, model)
    return (row["id"], Requirements.model_validate(row["data"])) if row else None


async def extract(
    llm: LLMClient, *, jd_text: str, jd_hash: str, title: str, run_id: int | None
) -> tuple[int, Requirements]:
    """Cached extraction. The LLM call happens outside any transaction; the result is stored afterwards."""
    async with transaction() as conn:
        hit = await cached(conn, jd_hash)
    if hit:
        return hit
    prompt = prompts.render(PROMPT, title=title, jd=fence("jd", jd_text))
    result = await llm.structured(TASK, prompt, Requirements, run_id=run_id, max_tokens=4000)
    prompt_version, model = cache_key()
    async with transaction() as conn:
        req_id = await repo.store(
            conn,
            jd_hash=jd_hash,
            data=result.output.model_dump(mode="json"),
            prompt_version=prompt_version,
            model_id=model,
        )
    return req_id, result.output
