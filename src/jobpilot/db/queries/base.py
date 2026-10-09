"""Version-checked updates: 'where id = x and version = n'. A conflict raises VersionConflict."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Table, update
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core import clock
from jobpilot.core.errors import VersionConflict


async def versioned_update(
    conn: AsyncConnection, table: Table, row_id: Any, expected_version: int, **values: Any
) -> int:
    """Apply `values` only if the row is still at `expected_version`. Returns the new version."""
    pk = next(iter(table.primary_key.columns))
    if "updated_at" in table.c:
        values.setdefault("updated_at", clock.now())
    result = await conn.execute(
        update(table)
        .where(pk == row_id, table.c.version == expected_version)
        .values(version=table.c.version + 1, **values)
        .returning(table.c.version)
    )
    new_version = result.scalar_one_or_none()
    if new_version is None:
        raise VersionConflict(f"{table.name} {row_id}: expected version {expected_version}")
    return new_version
