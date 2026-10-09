"""SQL for profile_items."""

from __future__ import annotations

from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core import clock
from jobpilot.db.queries.base import versioned_update
from jobpilot.db.tables import profile_items
from jobpilot.models.profile import DraftItem, ItemSource


async def create(
    conn: AsyncConnection,
    *,
    kind: str,
    text: str,
    evidence: str,
    source: str,
    attrs: dict[str, Any] | None = None,
    import_key: str | None = None,
    status: str = "draft",
) -> int:
    """Insert one profile item and return its id.

    Args:
        conn: An open transaction.
        kind: The item kind (skill, project, role, ...).
        text: The fact itself.
        evidence: Where it came from.
        source: resume, notes, chat or manual.
        attrs: Structured fields (e.g. a project's tech list).
        import_key: The importer's stable key, used to link details.
        status: ``draft`` or ``confirmed``.
    """
    row = await conn.execute(
        insert(profile_items)
        .values(
            kind=kind,
            text=text,
            evidence=evidence,
            source=source,
            attrs=attrs or {},
            import_key=import_key,
            status=status,
            confirmed_at=clock.now() if status == "confirmed" else None,
        )
        .returning(profile_items.c.id)
    )
    return int(row.scalar_one())


async def get(conn: AsyncConnection, item_id: int) -> RowMapping | None:
    """Return one profile item, or None."""
    row = await conn.execute(select(profile_items).where(profile_items.c.id == item_id))
    return row.mappings().one_or_none()


async def list_all(conn: AsyncConnection, *, statuses: tuple[str, ...] | None = None) -> list[RowMapping]:
    """Return profile items ordered by kind, optionally by status."""
    stmt = select(profile_items).order_by(profile_items.c.kind, profile_items.c.id)
    if statuses:
        stmt = stmt.where(profile_items.c.status.in_(statuses))
    return list((await conn.execute(stmt)).mappings())


async def by_ids(conn: AsyncConnection, ids: set[int]) -> dict[int, RowMapping]:
    """Map item id to row for the given ids."""
    if not ids:
        return {}
    rows = await conn.execute(select(profile_items).where(profile_items.c.id.in_(ids)))
    return {r["id"]: r for r in rows.mappings()}


async def set_status(conn: AsyncConnection, item_id: int, expected_version: int, status: str) -> int:
    """Version-checked status change; confirming stamps confirmed_at."""
    confirmed_at = clock.now() if status == "confirmed" else None
    return await versioned_update(
        conn, profile_items, item_id, expected_version, status=status, confirmed_at=confirmed_at
    )


async def edit_text(conn: AsyncConnection, item_id: int, expected_version: int, text: str) -> int:
    """Version-checked edit of an item's text."""
    return await versioned_update(conn, profile_items, item_id, expected_version, text=text)


async def edit_fields(
    conn: AsyncConnection, item_id: int, expected_version: int, *, text: str, attrs: dict[str, Any]
) -> int:
    """Version-checked edit of an item's text and attrs."""
    return await versioned_update(conn, profile_items, item_id, expected_version, text=text, attrs=attrs)


async def confirm_drafts(conn: AsyncConnection, ids: list[int] | None = None) -> int:
    """Confirm every draft (or only `ids`). Returns how many changed."""
    now = clock.now()
    stmt = (
        update(profile_items)
        .where(profile_items.c.status == "draft")
        .values(status="confirmed", confirmed_at=now, version=profile_items.c.version + 1, updated_at=now)
    )
    if ids is not None:
        stmt = stmt.where(profile_items.c.id.in_(ids))
    return (await conn.execute(stmt)).rowcount


async def store_drafts(conn: AsyncConnection, drafts: list[DraftItem], source: ItemSource) -> dict[str, int]:
    """Insert draft items; returns import key -> new item id."""
    ids = {}
    for d in drafts:
        ids[d.key] = await create(
            conn,
            kind=d.kind.value,
            text=d.text,
            evidence=d.evidence,
            source=source.value,
            attrs=dict(d.attrs),
            import_key=d.key,
        )
    return ids
