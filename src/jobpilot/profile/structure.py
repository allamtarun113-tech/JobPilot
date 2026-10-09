"""The profile as the page shows it, and the edits that keep that shape.

Each experience and each project with its own fields and its own
details, then education, skills (by category) and activities. Edits:
experience / project fields, a new experience or project, a detail
added under one, a detail moved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.queries import profile_items as repo
from jobpilot.models.profile import (
    DETAIL_KINDS,
    ENTRY_FIELDS,
    ItemSource,
    entry_fields,
    entry_headline,
    parent_ids,
    tech_list,
)

OTHER_LABELS = {"education": "Education", "activity": "Activities"}


@dataclass
class Entry:
    """One experience or project: its own item, its fields, and the details that belong to it."""

    item: RowMapping
    fields: dict[str, str]
    details: list[RowMapping] = field(default_factory=list)


@dataclass
class Layout:
    """The whole profile arranged for the profile page."""

    summary: list[RowMapping] = field(default_factory=list)
    experiences: list[Entry] = field(default_factory=list)
    projects: list[Entry] = field(default_factory=list)
    unattached: list[RowMapping] = field(default_factory=list)  # details not under any experience/project
    skills: dict[str, list[RowMapping]] = field(default_factory=dict)  # category -> skills
    others: list[tuple[str, list[RowMapping]]] = field(default_factory=list)  # education, activities...

    @property
    def entries(self) -> list[Entry]:
        """All experiences, then all projects."""
        return [*self.experiences, *self.projects]


async def layout(conn: AsyncConnection) -> Layout:
    """Arrange every profile item for the profile page.

    Args:
        conn: An open connection.

    Returns:
        The layout, with each detail under its experience or project.
    """
    rows = await repo.list_all(conn, statuses=("draft", "confirmed", "rejected"))
    parents = parent_ids(rows)
    out = Layout()
    entries: dict[int, Entry] = {}
    others: dict[str, list[RowMapping]] = {}
    # First pass: experiences and projects, so details can find them.
    for r in rows:
        kind = r["kind"]
        if kind in ENTRY_FIELDS:
            entries[r["id"]] = Entry(r, entry_fields(kind, r["text"], r["attrs"]))
            (out.experiences if kind == "role" else out.projects).append(entries[r["id"]])
    for r in rows:
        kind = r["kind"]
        if kind in ENTRY_FIELDS:
            continue
        if (pid := parents.get(r["id"])) in entries:
            entries[pid].details.append(r)  # type: ignore[index]
        elif kind == "summary":
            out.summary.append(r)
        elif kind == "skill":
            category = ((r["attrs"] or {}).get("categories") or ["Other skills"])[0]  # shown once
            out.skills.setdefault(category or "Other skills", []).append(r)
        elif kind in DETAIL_KINDS:
            out.unattached.append(r)
        else:
            others.setdefault(kind, []).append(r)
    order = [*OTHER_LABELS, *sorted(set(others) - set(OTHER_LABELS))]
    out.others = [(OTHER_LABELS.get(k, k.title()), others[k]) for k in order if k in others]
    return out


def _clean(kind: str, values: dict[str, str]) -> tuple[str, dict[str, Any]]:
    """Form values -> (item text, attrs fields). The name / title is required."""
    fields: dict[str, Any] = {key: " ".join(values.get(key, "").split()) for key, _ in ENTRY_FIELDS[kind]}
    fields["about"] = values.get("about", "").strip()
    fields["tech"] = tech_list({"tech": values.get("tech", "")})
    required = "title" if kind == "role" else "name"
    if not fields[required]:
        raise ValueError("an experience needs a job title" if kind == "role" else "a project needs a name")
    return entry_headline(kind, {**fields, "tech": ""}), fields


async def add_entry(conn: AsyncConnection, kind: str, values: dict[str, str]) -> int:
    """A new experience or project typed on the profile page (yours, so confirmed)."""
    if kind not in ENTRY_FIELDS:
        raise ValueError(kind)
    text, fields = _clean(kind, values)
    return await repo.create(
        conn,
        kind=kind,
        text=text,
        evidence="Added on the profile page",
        source=ItemSource.MANUAL.value,
        attrs=fields,
        status="confirmed",
    )


async def save_fields(conn: AsyncConnection, item_id: int, version: int, values: dict[str, str]) -> int:
    """Version-checked save of an experience's or project's fields.

    Raises:
        ValueError: The item isn't an experience or project, or the
            name/title is missing.
    """
    item = await repo.get(conn, item_id)
    if item is None or item["kind"] not in ENTRY_FIELDS:
        raise ValueError("only an experience or a project has fields")
    text, fields = _clean(item["kind"], values)
    return await repo.edit_fields(
        conn, item_id, version, text=text, attrs={**(item["attrs"] or {}), **fields}
    )


async def add_detail(conn: AsyncConnection, parent_id: int, kind: str, text: str) -> int:
    """A detail (what you did, a result with a number) typed under one experience or project."""
    parent = await repo.get(conn, parent_id)
    if parent is None or parent["kind"] not in ENTRY_FIELDS:
        raise ValueError("details go under an experience or a project")
    if kind not in DETAIL_KINDS:
        raise ValueError(kind)
    text = " ".join(text.split())
    if not text:
        raise ValueError("a detail needs text")
    return await repo.create(
        conn,
        kind=kind,
        text=text,
        evidence=f"Added on the profile page under {parent['text']}",
        source=ItemSource.MANUAL.value,
        attrs={"parent_id": parent_id},
        status="confirmed",
    )


async def move_detail(conn: AsyncConnection, item_id: int, version: int, parent_id: int | None) -> int:
    """Put a detail under another experience / project (or under none)."""
    item = await repo.get(conn, item_id)
    if item is None or item["kind"] not in DETAIL_KINDS:
        raise ValueError("only a detail can be moved")
    if parent_id is not None:
        parent = await repo.get(conn, parent_id)
        if parent is None or parent["kind"] not in ENTRY_FIELDS:
            raise ValueError("details go under an experience or a project")
    # Drop the old link (both forms) before setting the new one.
    attrs = {k: v for k, v in (item["attrs"] or {}).items() if k not in ("parent", "parent_id")}
    if parent_id is not None:
        attrs["parent_id"] = parent_id
    return await repo.edit_fields(conn, item_id, version, text=item["text"], attrs=attrs)
