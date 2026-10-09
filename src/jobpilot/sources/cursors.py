"""Per-source cursors (source_cursors).

Saved only when a source's fetch succeeded, in the same transaction as
the jobs it produced; a skipped day or weekend loses nothing.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.queries import source_cursors


async def load(conn: AsyncConnection, source: str) -> tuple[dict[str, Any] | None, int | None]:
    """(cursor, version) - (None, None) on the first run."""
    row = await source_cursors.get(conn, source)
    return (dict(row["cursor"]), row["version"]) if row else (None, None)


async def save(conn: AsyncConnection, source: str, cursor: dict[str, Any], version: int | None) -> int:
    """Save a source's new cursor, version-checked.

    Args:
        conn: The transaction that also stores the source's jobs.
        source: The source name.
        cursor: The new cursor.
        version: The version read with ``load`` (None on the first
            save).

    Returns:
        The new version.
    """
    return await source_cursors.save(conn, source, cursor, version)
