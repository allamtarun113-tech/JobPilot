"""SQL for requirements (LLM extraction cache keyed by JD hash + prompt version + model id)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.tables import requirements


async def cached(
    conn: AsyncConnection, jd_hash: str, prompt_version: str, model_id: str
) -> RowMapping | None:
    """Return the cached extraction for this JD, prompt and model, or None."""
    row = await conn.execute(
        select(requirements).where(
            requirements.c.jd_hash == jd_hash,
            requirements.c.prompt_version == prompt_version,
            requirements.c.model_id == model_id,
        )
    )
    return row.mappings().one_or_none()


async def get(conn: AsyncConnection, req_id: int) -> RowMapping | None:
    """Return one requirements row, or None."""
    return (
        (await conn.execute(select(requirements).where(requirements.c.id == req_id))).mappings().one_or_none()
    )


async def store(
    conn: AsyncConnection, *, jd_hash: str, data: dict[str, Any], prompt_version: str, model_id: str
) -> int:
    """Store (or overwrite) an extraction in the cache.

    Returns:
        The requirements row id.
    """
    stmt = (
        insert(requirements)
        .values(jd_hash=jd_hash, data=data, prompt_version=prompt_version, model_id=model_id)
        .on_conflict_do_update(
            index_elements=[requirements.c.jd_hash, requirements.c.prompt_version, requirements.c.model_id],
            set_={"data": data},
        )
        .returning(requirements.c.id)
    )
    return int((await conn.execute(stmt)).scalar_one())
