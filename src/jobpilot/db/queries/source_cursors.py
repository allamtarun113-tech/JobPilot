"""SQL for source_cursors.

A cursor is saved only when its source's fetch succeeds, in the same
transaction as the jobs it produced.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core.errors import VersionConflict
from jobpilot.db.queries.base import versioned_update
from jobpilot.db.tables import source_cursors


async def get(conn: AsyncConnection, source: str) -> RowMapping | None:
    """Return a source's cursor row, or None before its first save."""
    row = await conn.execute(select(source_cursors).where(source_cursors.c.source == source))
    return row.mappings().one_or_none()


async def save(
    conn: AsyncConnection, source: str, cursor: dict[str, Any], expected_version: int | None
) -> int:
    """Insert the first cursor (expected_version=None) or advance an existing one, version-checked."""
    if expected_version is None:  # first save; a concurrent first save is a version conflict, not a crash
        stmt = (
            insert(source_cursors)
            .values(source=source, cursor=cursor)
            .on_conflict_do_nothing(index_elements=[source_cursors.c.user_id, source_cursors.c.source])
            .returning(source_cursors.c.id)
        )
        if (await conn.execute(stmt)).scalar_one_or_none() is None:
            raise VersionConflict(f"source cursor {source} was created concurrently")
        return 1
    existing = await get(conn, source)
    assert existing is not None
    return await versioned_update(conn, source_cursors, existing["id"], expected_version, cursor=cursor)
