"""Profile items: store drafts, list them, and edit them (every write version-checked)."""

from __future__ import annotations

from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.queries import profile_items as repo
from jobpilot.models.profile import DraftItem, ItemSource


async def store_drafts(conn: AsyncConnection, drafts: list[DraftItem], source: ItemSource) -> dict[str, int]:
    """Insert draft items; returns import key -> new item id."""
    return await repo.store_drafts(conn, drafts, source)


async def grouped(conn: AsyncConnection, include_rejected: bool = False) -> dict[str, list[RowMapping]]:
    """Return profile items grouped by kind (rejected ones only if asked)."""
    statuses = ("draft", "confirmed", "rejected") if include_rejected else ("draft", "confirmed")
    out: dict[str, list[RowMapping]] = {}
    for row in await repo.list_all(conn, statuses=statuses):
        out.setdefault(row["kind"], []).append(row)
    return out


async def places(conn: AsyncConnection) -> dict[str, int]:
    """Experience / project name -> item id, for filing a note under the one it is about."""
    rows = await repo.list_all(conn, statuses=("draft", "confirmed"))
    return {str(r["text"]): int(r["id"]) for r in rows if r["kind"] in ("role", "project")}


async def skill_names(conn: AsyncConnection) -> list[str]:
    """The skills already in the profile (draft or confirmed), so notes don't add them again."""
    rows = await repo.list_all(conn, statuses=("draft", "confirmed"))
    return [str(r["text"]) for r in rows if r["kind"] == "skill"]
