"""The structured resume: sections -> entries -> bullets, each with an id and the profile items it rests on.

Rendered only through the one fixed LaTeX template (templates/resume/resume.tex.j2).
Tailoring edits a copy of this document with operations (move, rewrite, hide);
the LLM never writes LaTeX.
"""

from __future__ import annotations

from collections.abc import Iterator
from enum import StrEnum

from pydantic import BaseModel, Field


class Layout(StrEnum):
    """How a resume section is laid out by the template."""

    PARAGRAPH = "paragraph"  # one block of text (career objective / summary)
    DATED_PAIRS = "dated_pairs"  # education: title + date, then subtitle + detail
    SKILLS = "skills"  # label: comma-separated items
    PROJECTS = "projects"  # bold title, optional note line, bullets
    ROLES = "roles"  # sans-serif bold title + date, bullets
    BULLETS = "bullets"  # a plain bullet list (activities)


class Bullet(BaseModel):
    """One resume bullet and the profile items that back it."""

    id: str
    text: str
    sub: str | None = None  # an optional second, indented line
    evidence: list[int] = Field(default_factory=list)  # profile_item ids
    hidden: bool = False


class Entry(BaseModel):
    """One entry in a section (a role, project, degree or skills line)."""

    id: str
    title: str | None = None
    date: str | None = None
    subtitle: str | None = None
    detail: str | None = None
    note: str | None = None
    text: str | None = None  # paragraph layout
    items: list[str] = Field(default_factory=list)  # skills layout
    bullets: list[Bullet] = Field(default_factory=list)
    evidence: list[int] = Field(default_factory=list)
    hidden: bool = False


class Section(BaseModel):
    """One resume section with its layout and entries."""

    id: str
    title: str
    layout: Layout
    entries: list[Entry] = Field(default_factory=list)
    hidden: bool = False


class Contact(BaseModel):
    """One contact item in the resume header, optionally a link."""

    text: str
    url: str | None = None
    underline: bool = False


class Header(BaseModel):
    """The resume header: the name and the contact line."""

    name: str
    contacts: list[Contact] = Field(default_factory=list)


class ResumeDoc(BaseModel):
    """The whole structured resume, rendered by the fixed template."""

    header: Header
    sections: list[Section]
    template_id: str = "resume_v1"
    max_pages: int = 1
    composed: bool = False  # built by the resume v3 composer from the profile (decision 36)

    def visible_sections(self) -> Iterator[Section]:
        """Yield the sections that aren't hidden, in order."""
        return (s for s in self.sections if not s.hidden)

    def elements(self) -> Iterator[tuple[Section, Entry, Bullet | None]]:
        """Every visible entry and bullet, in document order."""
        for section in self.visible_sections():
            for entry in section.entries:
                if entry.hidden:
                    continue
                yield section, entry, None
                for bullet in entry.bullets:
                    if not bullet.hidden:
                        yield section, entry, bullet

    def evidence_ids(self) -> set[int]:
        """Return every profile item id the visible resume rests on."""
        ids: set[int] = set()
        for _, entry, bullet in self.elements():
            ids.update(bullet.evidence if bullet else entry.evidence)
        return ids
