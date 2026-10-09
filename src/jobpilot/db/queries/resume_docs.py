"""SQL for resume_doc (the structured base resume)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.queries.base import versioned_update
from jobpilot.db.tables import resume_doc


async def current(conn: AsyncConnection) -> RowMapping | None:
    """Return the current base resume row, or None."""
    row = await conn.execute(select(resume_doc).where(resume_doc.c.is_current))
    return row.mappings().one_or_none()


async def replace_current(
    conn: AsyncConnection, *, doc: dict[str, Any], template_id: str, source_file: str
) -> int:
    """Retire the current base resume (kept for history) and insert the new one as current."""
    await conn.execute(update(resume_doc).where(resume_doc.c.is_current).values(is_current=False))
    row = await conn.execute(
        insert(resume_doc)
        .values(doc=doc, template_id=template_id, source_file=source_file, is_current=True)
        .returning(resume_doc.c.id)
    )
    return int(row.scalar_one())


async def save_render(
    conn: AsyncConnection,
    row_id: int,
    expected_version: int,
    *,
    pdf_key: str,
    parse_ok: bool,
    report: dict[str, Any],
) -> int:
    """Version-checked save of the base resume's rendered PDF and checks."""
    return await versioned_update(
        conn, resume_doc, row_id, expected_version, pdf_key=pdf_key, parse_ok=parse_ok, render_report=report
    )
