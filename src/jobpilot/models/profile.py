"""Profile vocabulary: the user's facts as individual items with ids and evidence.

The confirmed profile is the single source of truth for scoring, tailoring and the truthfulness check.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ItemKind(StrEnum):
    """The kinds of facts a profile item can hold."""

    SKILL = "skill"
    PROJECT = "project"
    ROLE = "role"
    EDUCATION = "education"
    METRIC = "metric"
    ACHIEVEMENT = "achievement"  # one factual statement, e.g. a resume bullet
    ACTIVITY = "activity"
    SUMMARY = "summary"  # the career objective / summary paragraph


class ItemSource(StrEnum):
    """Where a profile item came from."""

    RESUME = "resume"
    NOTES = "notes"
    CHAT = "chat"
    MANUAL = "manual"  # typed by the user on the profile page


class DraftItem(BaseModel):
    """A proposed profile item (from the resume importer or the notes parser), before confirmation."""

    key: str  # stable within one import, e.g. "skill:python"; used to link resume elements
    kind: ItemKind
    text: str
    evidence: str  # where it came from (section path or a quote from the notes)
    attrs: dict[str, str | int | list[str] | None] = Field(default_factory=dict)


# ---- Experiences and projects: structured fields, their details, and the facts other features read ----

# The fields of an experience (role) or a project, kept in profile_items.attrs. "tech" is a list.
ENTRY_FIELDS: dict[str, list[tuple[str, str]]] = {
    "role": [
        ("title", "Job title"),
        ("organization", "Company / organization"),
        ("dates", "Dates (e.g. Jun 2024 - Present)"),
        ("location", "Location"),
        ("employment_type", "Type (full-time, internship, contract...)"),
        ("team", "Team / product area"),
        ("tech", "Tools and technologies used (comma-separated)"),
        ("about", "What the role was about"),
    ],
    "project": [
        ("name", "Project name"),
        ("your_role", "Your role"),
        ("dates", "Dates"),
        ("context", "Context (personal, academic, at a company...)"),
        ("link", "Link (GitHub, demo, paper)"),
        ("tech", "Tools and technologies used (comma-separated)"),
        ("about", "What the project is / the problem it solves"),
    ],
}
DETAIL_KINDS = ("achievement", "metric")  # what can sit under an experience or a project


def tech_list(attrs: Mapping[str, Any] | None) -> list[str]:
    """Return the tools listed in an item's ``tech`` attribute.

    Args:
        attrs: The item's attrs; ``tech`` may be a list or a
            comma-separated string.

    Returns:
        The tool names, stripped, with empty entries dropped.
    """
    tech = (attrs or {}).get("tech") or []
    if isinstance(tech, str):
        tech = tech.split(",")
    return [t.strip() for t in tech if str(t).strip()]


def entry_fields(kind: str, text: str, attrs: Mapping[str, Any] | None) -> dict[str, str]:
    """The editable fields of an experience or project, filled from attrs (older items: from the text)."""
    attrs = attrs or {}
    fields = {key: str(attrs.get(key) or "") for key, _ in ENTRY_FIELDS.get(kind, [])}
    if "tech" in fields:
        fields["tech"] = ", ".join(tech_list(attrs))
    if kind == "project" and not fields["about"] and fields["name"] and text.startswith(fields["name"]):
        fields["about"] = text[len(fields["name"]) :].lstrip(" -–")  # imported "Name - note"
    if kind == "role" and not fields["title"]:
        fields["title"] = text
    if kind == "project" and not fields["name"]:
        fields["name"] = text
    return fields


def entry_headline(kind: str, fields: Mapping[str, str]) -> str:
    """The item text of an experience ("Data Scientist at Turing (Jun 2024 - Present)") or a project."""
    if kind == "role":
        text = fields.get("title", "").strip()
        if org := fields.get("organization", "").strip():
            text = f"{text} at {org}" if text else org
        if dates := fields.get("dates", "").strip():
            text += f" ({dates})"
        return text
    return fields.get("name", "").strip()


def parent_ids(rows: Iterable[Mapping[Any, Any]]) -> dict[int, int]:
    """Map each detail item id to its experience or project item id.

    A detail names its parent by ``attrs.parent_id``, or (from the
    resume importer) by ``attrs.parent``: the newest item with that
    import key created before the detail.

    Args:
        rows: Profile item rows with ``id``, ``attrs`` and
            ``import_key``.

    Returns:
        Detail item id -> parent item id.
    """
    rows = list(rows)
    ids = {int(r["id"]) for r in rows}
    by_key: dict[str, list[int]] = {}
    for r in rows:
        if r.get("import_key"):
            by_key.setdefault(str(r["import_key"]), []).append(int(r["id"]))
    out: dict[int, int] = {}
    for r in rows:
        attrs = r.get("attrs") or {}
        pid = attrs.get("parent_id")
        if isinstance(pid, int) and pid in ids:
            out[int(r["id"])] = pid
        elif key := attrs.get("parent"):
            # Importer keys can repeat across imports: pick the newest
            # matching item that existed before this detail.
            earlier = [i for i in by_key.get(str(key), []) if i < int(r["id"])]
            if earlier:
                out[int(r["id"])] = max(earlier)
    return out


def fact_texts(rows: Iterable[Mapping[Any, Any]]) -> dict[int, str]:
    """Map item id to the fact text the AI features read.

    An experience or project gets its structured fields appended, and
    a detail gets the text of the item it belongs to in brackets. The
    tailor, email writer, Job Chat and Prepare all read these texts.

    Args:
        rows: Profile item rows with ``id``, ``kind``, ``text``,
            ``attrs``.

    Returns:
        Item id -> fact text.
    """
    rows = list(rows)
    by_id = {int(r["id"]): r for r in rows}
    parents = parent_ids(rows)
    out: dict[int, str] = {}
    for r in rows:
        text, kind, attrs = str(r["text"]), str(r["kind"]), r.get("attrs") or {}
        if kind in ENTRY_FIELDS:
            fields = entry_fields(kind, text, attrs)
            # Add only the fields the headline doesn't already say.
            extras = [
                f"{label.split(' (')[0]}: {fields[key]}"
                for key, label in ENTRY_FIELDS[kind]
                if fields.get(key) and fields[key] not in text
            ]
            # Drop trailing dots and spaces before joining with ". ".
            text = re.sub(r"[.\s]+$", "", text)
            text = ". ".join([text, *extras]) if extras else text
        if (pid := parents.get(int(r["id"]))) is not None:
            text = f"{text} [{by_id[pid]['text']}]"
        out[int(r["id"])] = text
    return out


# ---- LLM output schema for profile/parser.py (notes -> draft items) -------------------------------


class ExtractedItem(BaseModel):
    """One profile fact the notes parser found, with its evidence quote."""

    kind: ItemKind
    text: str = Field(description="One self-contained fact in the user's own terms. No invented details.")
    evidence_quote: str = Field(description="The exact words from the notes this item rests on.")
    belongs_to: str = Field(
        default="",
        description="For a detail: its experience or project's name, listed or from these notes. Else ''.",
    )
    about: str = Field(default="", description="A project or role only: what it is, from the notes. Else ''.")
    tech: list[str] = Field(
        default_factory=list, description="A project or role only: the tools the notes say it uses. Else []."
    )


class ExtractedProfile(BaseModel):
    """Every profile fact the notes parser found in one set of notes."""

    items: list[ExtractedItem]
