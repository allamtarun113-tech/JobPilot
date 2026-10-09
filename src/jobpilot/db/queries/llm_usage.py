"""SQL for llm_usage."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.tables import llm_usage


async def record(conn: AsyncConnection, **values: object) -> None:
    """Log one LLM call's model, tokens and cost."""
    await conn.execute(insert(llm_usage).values(**values))


async def tokens_since(conn: AsyncConnection, since: datetime) -> int:
    """Return input + output tokens used since a time."""
    total = await conn.execute(
        select(func.coalesce(func.sum(llm_usage.c.input_tokens + llm_usage.c.output_tokens), 0)).where(
            llm_usage.c.created_at >= since
        )
    )
    return int(total.scalar_one())
