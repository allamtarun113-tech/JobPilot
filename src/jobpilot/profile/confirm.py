"""The one-time confirmation of draft profile items.

The user confirms (or edits / rejects) draft items. Afterwards every
resume bullet must rest on confirmed items - backing_report() checks
that.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.queries import profile_items as repo
from jobpilot.models.resume_doc import ResumeDoc


@dataclass
class BackingReport:
    """How many resume elements rest only on confirmed items, and which don't."""

    total: int = 0
    backed: int = 0
    unbacked: list[str] = field(default_factory=list)  # element ids with missing or unconfirmed evidence

    @property
    def ok(self) -> bool:
        """True when there is content and every element is backed."""
        return self.total > 0 and not self.unbacked


async def backing_report(conn: AsyncConnection, doc: ResumeDoc) -> BackingReport:
    """Every visible entry with content and every visible bullet must cite only confirmed items."""
    items = await repo.by_ids(conn, doc.evidence_ids())
    report = BackingReport()
    for _, entry, bullet in doc.elements():
        evidence = bullet.evidence if bullet else entry.evidence
        # An empty entry (a bare heading) needs no evidence.
        if bullet is None and not entry.bullets and not (entry.title or entry.text or entry.items):
            continue
        report.total += 1
        if evidence and all(i in items and items[i]["status"] == "confirmed" for i in evidence):
            report.backed += 1
        else:
            report.unbacked.append(bullet.id if bullet else entry.id)
    return report


async def confirm_all(conn: AsyncConnection) -> int:
    """Confirm every draft item; return how many changed."""
    return await repo.confirm_drafts(conn)


async def set_status(conn: AsyncConnection, item_id: int, version: int, status: str) -> int:
    """Version-checked status change to draft, confirmed or rejected.

    Raises:
        ValueError: An unknown status.
    """
    if status not in ("draft", "confirmed", "rejected"):
        raise ValueError(status)
    return await repo.set_status(conn, item_id, version, status)


async def edit_text(conn: AsyncConnection, item_id: int, version: int, text: str) -> int:
    """Version-checked edit of an item's text.

    Raises:
        ValueError: The text is empty.
    """
    text = text.strip()
    if not text:
        raise ValueError("an item needs text")
    return await repo.edit_text(conn, item_id, version, text)
