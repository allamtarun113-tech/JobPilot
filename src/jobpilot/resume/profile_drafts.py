"""Resume -> draft profile items (deterministic).

One item per skill, education entry, role, project, bullet and
activity, plus the summary; and which items each resume element rests
on. Part of importing the user's .tex; the user confirms the items on
the dashboard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from jobpilot.models.profile import DraftItem, ItemKind
from jobpilot.models.resume_doc import Entry, Layout, ResumeDoc
from jobpilot.reference import catalog


@dataclass
class ResumeDraft:
    """Draft items from a resume and the items each element rests on."""

    items: list[DraftItem] = field(default_factory=list)
    links: dict[str, list[str]] = field(default_factory=dict)  # resume element id -> item keys

    def add(self, item: DraftItem) -> str:
        """Add an item unless one with the same key exists; return its key."""
        if all(i.key != item.key for i in self.items):
            self.items.append(item)
        return item.key


def _skill_key(name: str) -> str:
    """De-duplicate by the user's own wording ("Neural Networks" stays separate from "Deep Learning"
    even though the taxonomy maps both to one id, kept in attrs.skill_id)."""
    return "skill:" + re.sub(r"[^a-z0-9+#]+", "-", name.lower()).strip("-")


def _split_role(title: str) -> tuple[str, str]:
    """'Turing - Delivery Data Scientist.' -> ('Turing', 'Delivery Data Scientist')."""
    org, sep, role = title.rstrip(". ").partition(" - ")
    return (org.strip(), role.strip()) if sep else ("", title.rstrip(". "))


def draft_from_resume(doc: ResumeDoc) -> ResumeDraft:
    """Create draft profile items from every element of a resume.

    Args:
        doc: The imported resume.

    Returns:
        The items and the element -> item keys links.
    """
    draft = ResumeDraft()
    skills: dict[str, DraftItem] = {}
    for section in doc.sections:
        for entry in section.entries:
            if section.layout == Layout.SKILLS:
                keys = []
                for name in entry.items:
                    key = _skill_key(name)
                    path = f"{section.title} > {entry.title}"
                    # A skill listed on several lines is one item with
                    # every category.
                    if key in skills:
                        cats = skills[key].attrs.setdefault("categories", [])
                        assert isinstance(cats, list)
                        if entry.title not in cats:
                            cats.append(entry.title or "")
                            skills[key].evidence += f"; {path}"
                    else:
                        skills[key] = DraftItem(
                            key=key,
                            kind=ItemKind.SKILL,
                            text=name,
                            evidence=path,
                            attrs={"skill_id": catalog.skill_id(name), "categories": [entry.title or ""]},
                        )
                        draft.add(skills[key])
                    keys.append(key)
                draft.links[entry.id] = keys
            elif section.layout == Layout.PARAGRAPH:
                key = draft.add(
                    DraftItem(
                        key=f"summary:{entry.id}",
                        kind=ItemKind.SUMMARY,
                        text=entry.text or "",
                        evidence=section.title,
                    )
                )
                draft.links[entry.id] = [key]
            elif section.layout == Layout.DATED_PAIRS:
                text = ", ".join(x for x in (entry.title, entry.subtitle, entry.date, entry.detail) if x)
                key = draft.add(
                    DraftItem(
                        key=f"education:{entry.id}",
                        kind=ItemKind.EDUCATION,
                        text=text,
                        evidence=section.title,
                        attrs={
                            "qualification": entry.title,
                            "institution": entry.subtitle,
                            "dates": entry.date,
                            "grade": entry.detail,
                        },
                    )
                )
                draft.links[entry.id] = [key]
            elif section.layout in (Layout.ROLES, Layout.PROJECTS):
                parent = _parent_item(section.title, section.layout, entry)
                draft.add(parent)
                draft.links[entry.id] = [parent.key]
                for bullet in entry.bullets:
                    key = draft.add(
                        DraftItem(
                            key=f"achievement:{bullet.id}",
                            kind=ItemKind.ACHIEVEMENT,
                            text=bullet.text,
                            evidence=f"{section.title} > {entry.title}",
                            attrs={"parent": parent.key},
                        )
                    )
                    # A bullet rests on its own achievement and its
                    # role/project.
                    draft.links[bullet.id] = [key, parent.key]
            elif section.layout == Layout.BULLETS:
                for bullet in entry.bullets:
                    text = f"{bullet.text} - {bullet.sub}" if bullet.sub else bullet.text
                    key = draft.add(
                        DraftItem(
                            key=f"activity:{bullet.id}",
                            kind=ItemKind.ACTIVITY,
                            text=text,
                            evidence=section.title,
                        )
                    )
                    draft.links[bullet.id] = [key]
                draft.links[entry.id] = [k for b in entry.bullets for k in draft.links[b.id]]
    return draft


def _parent_item(section_title: str, layout: Layout, entry: Entry) -> DraftItem:
    """Return the draft item for a role or project entry."""
    if layout == Layout.ROLES:
        org, role = _split_role(entry.title or "")
        text = f"{role} at {org}" if org else role
        if entry.date:
            text += f" ({entry.date})"
        return DraftItem(
            key=f"role:{entry.id}",
            kind=ItemKind.ROLE,
            text=text,
            evidence=section_title,
            attrs={"organization": org, "title": role, "dates": entry.date},
        )
    text = entry.title or ""
    if entry.note:
        text += f" - {entry.note}"
    return DraftItem(
        key=f"project:{entry.id}",
        kind=ItemKind.PROJECT,
        text=text,
        evidence=section_title,
        attrs={"name": entry.title},
    )
