"""Everything a tailored resume may hold (resume v3, decision 36): the base resume plus the confirmed profile.

Each confirmed experience and project becomes one entry. An entry on the base resume keeps its title, date
and note; one that isn't (a project added on the profile page) gets them from its profile fields. Every
confirmed detail of an entry is its own bullet - details are never merged, and a detail only ever sits
under the entry it belongs to. Skills are the base skills lines plus confirmed skills the base lacks.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from jobpilot.core.config import get_config
from jobpilot.models.profile import entry_fields, parent_ids
from jobpilot.models.resume_doc import Layout, ResumeDoc
from jobpilot.reference.catalog import display_skill
from jobpilot.resume import polish
from jobpilot.safety.fidelity import clean


@dataclass
class PoolBullet:
    """One fact that may be a bullet: its original text and the profile items behind it."""

    id: str
    text: str
    sub: str | None = None
    evidence: list[int] = field(default_factory=list)


@dataclass
class PoolEntry:
    """One experience or project that may go on the resume."""

    id: str
    kind: str  # "role" | "project"
    title: str
    date: str | None
    note: str | None
    bullets: list[PoolBullet]
    evidence: list[int]
    context: str  # the entry's own words (title, note, fields): they back words in its bullets
    on_base: bool  # on the base resume


@dataclass
class Pool:
    """The base resume's layout plus every confirmed entry, detail and skill."""

    roles: list[PoolEntry]
    projects: list[PoolEntry]
    skill_lines: list[tuple[str, list[str]]]
    extra_skills: list[str]
    summary: str
    facts: list[str]
    role_titles: list[str]
    max_chars: int = 210  # a bullet longer than this runs past two lines
    known: frozenset[str] = frozenset()  # words the spell check never changes (polish.known_words)
    role_heads: list[tuple[str, str]] = field(default_factory=list)  # (title, organization), current first
    project_abouts: list[tuple[str, str]] = field(default_factory=list)  # (name, what it is)
    degrees: list[tuple[str, str]] = field(default_factory=list)  # (qualification, institution)

    def entry(self, entry_id: str) -> PoolEntry | None:
        """Return the entry with this id, or None."""
        return next((e for e in [*self.roles, *self.projects] if e.id == entry_id), None)


def skill_key(text: str) -> str:
    """A skill's identity for de-duplication: its display name, lower-cased."""
    return re.sub(r"\s*\(.*?\)", "", display_skill(text)).lower()


def _context(kind: str, fields: Mapping[str, str], title: str, note: str | None) -> str:
    """The words an entry's bullets may lean on: its title, note and every profile field."""
    return "\n".join(v for v in [title, note or "", *fields.values()] if v)


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def split_long(bullets: list[PoolBullet], max_chars: int) -> list[PoolBullet]:
    """Split a fact longer than two lines into bullets of whole sentences, each within `max_chars`.

    The sentences stay in their order under the same entry and keep the fact's evidence; nothing is reworded.
    A single sentence that is still too long stays whole (the composer is asked to shorten it).
    """
    out: list[PoolBullet] = []
    for b in bullets:
        parts = [p.strip() for p in _SENTENCE_END.split(b.text) if p.strip()]
        if len(b.text) <= max_chars or b.sub or len(parts) < 2:
            out.append(b)
            continue
        pieces, current = [], ""
        for part in parts:
            if current and len(current) + 1 + len(part) > max_chars:
                pieces.append(current)
                current = part
            else:
                current = f"{current} {part}".strip()
        pieces.append(current)
        out += [
            PoolBullet(b.id if n == 1 else f"{b.id}.{n}", piece, None, list(b.evidence))
            for n, piece in enumerate(pieces, 1)
        ]
    return out


def build(base: ResumeDoc, rows: Iterable[Mapping[Any, Any]], max_chars: int | None = None) -> Pool:
    """Collect the base resume and the confirmed profile into one pool.

    Args:
        base: The base resume.
        rows: Confirmed profile item rows (id, kind, text, attrs, import_key).
        max_chars: The two-line bullet limit (config resume.max_bullet_chars by default).

    Returns:
        The pool the composer picks from.
    """
    max_chars = max_chars or get_config().resume.max_bullet_chars
    rows = [r for r in rows if r.get("status", "confirmed") == "confirmed"]
    by_id = {int(r["id"]): r for r in rows}
    parents = parent_ids(rows)
    details: dict[int, list[int]] = {}
    for child, parent in sorted(parents.items()):
        details.setdefault(parent, []).append(child)
    used = {i for s in base.sections for e in s.entries for b in e.bullets for i in b.evidence}
    pool = Pool([], [], [], [], "", [clean(str(r["text"])) for r in rows], [], max_chars)
    placed: set[int] = set()
    for section in base.sections:
        if section.layout == Layout.PARAGRAPH and section.entries and not pool.summary:
            pool.summary = section.entries[0].text or ""
        if section.layout == Layout.SKILLS:
            pool.skill_lines += [(e.title or "", list(e.items)) for e in section.entries if not e.hidden]
        if section.layout not in (Layout.ROLES, Layout.PROJECTS):
            continue
        kind = "role" if section.layout == Layout.ROLES else "project"
        for e in section.entries:
            item = next((i for i in e.evidence if i in by_id and by_id[i]["kind"] == kind), None)
            fields = entry_fields(kind, str(by_id[item]["text"]), by_id[item]["attrs"] or {}) if item else {}
            bullets = [
                PoolBullet(b.id, clean(b.text), clean(b.sub) if b.sub else None, list(b.evidence))
                for b in e.bullets
            ]
            if item is not None:
                placed.add(item)
                bullets += [
                    PoolBullet(f"p{d}", clean(str(by_id[d]["text"])), None, [d, item])
                    for d in details.get(item, [])
                    if d not in used and by_id[d]["kind"] in ("achievement", "metric")
                ]
            title = e.title or ""
            entry = PoolEntry(
                e.id,
                kind,
                title,
                e.date,
                e.note,
                bullets,
                list(e.evidence),
                _context(kind, fields, title, e.note),
                True,
            )
            (pool.roles if kind == "role" else pool.projects).append(entry)
    for row in rows:
        kind, item = str(row["kind"]), int(row["id"])
        if kind not in ("role", "project") or item in placed:
            continue
        fields = entry_fields(kind, str(row["text"]), row["attrs"] or {})
        if kind == "role":
            title = " - ".join(p for p in (fields.get("organization"), fields.get("title")) if p) or str(
                row["text"]
            )
            note = None
        else:
            title, note = fields.get("name") or str(row["text"]), clean(fields.get("about") or "") or None
        bullets = [
            PoolBullet(f"p{d}", clean(str(by_id[d]["text"])), None, [d, item])
            for d in details.get(item, [])
            if by_id[d]["kind"] in ("achievement", "metric")
        ]
        entry = PoolEntry(
            f"p{item}",
            kind,
            clean(title),
            fields.get("dates") or None,
            note,
            bullets,
            [item],
            _context(kind, fields, title, note),
            False,
        )
        (pool.roles if kind == "role" else pool.projects).append(entry)
    names = {
        w
        for r in rows
        if r["kind"] in ("role", "project", "skill", "education")
        for w in re.findall(
            r"[A-Za-z][A-Za-z0-9+#]*", " ".join([str(r["text"]), *map(str, (r["attrs"] or {}).values())])
        )
    } | {w for e in [*pool.roles, *pool.projects] for w in re.findall(r"[A-Za-z][A-Za-z0-9+#]*", e.title)}
    pool.known = polish.known_words(names)
    pool.facts += [e.context for e in [*pool.roles, *pool.projects]]  # entry fields back the summary too
    pool.facts += [display_skill(str(r["text"])) for r in rows if r["kind"] == "skill"]  # proper names
    for pooled in [*pool.roles, *pool.projects]:  # the composer sees (and rewords) correctly spelled facts
        pooled.note = polish.sentence(pooled.note, pool.known) if pooled.note else pooled.note
        for b in pooled.bullets:
            b.text = polish.sentence(b.text, pool.known)
            b.sub = polish.sentence(b.sub, pool.known) if b.sub else b.sub
        pooled.bullets = split_long(pooled.bullets, max_chars)
    for row in rows:  # the facts a written-by-code objective may use
        attrs = row["attrs"] or {}
        if row["kind"] == "role":
            f = entry_fields("role", str(row["text"]), attrs)
            pool.role_heads.append((f.get("title", ""), f.get("organization", "")))
        elif row["kind"] == "project":
            f = entry_fields("project", str(row["text"]), attrs)
            if f.get("about"):
                pool.project_abouts.append((f.get("name", ""), clean(f["about"])))
        elif row["kind"] == "education" and attrs.get("qualification") and attrs.get("institution"):
            pool.degrees.append((str(attrs["qualification"]), str(attrs["institution"])))
    for row in rows:
        if row["kind"] == "role":
            pool.role_titles.append(
                entry_fields("role", str(row["text"]), row["attrs"] or {}).get("title", "")
            )
    shown = {skill_key(s) for _label, items in pool.skill_lines for s in items}
    for row in rows:
        if row["kind"] == "skill" and skill_key(str(row["text"])) not in shown:
            shown.add(skill_key(str(row["text"])))
            pool.extra_skills.append(display_skill(str(row["text"])))
    return pool


def as_prompt(pool: Pool) -> dict[str, Any]:
    """The pool as the composer sees it: ids and original texts."""

    def entry(e: PoolEntry) -> dict[str, Any]:
        out: dict[str, Any] = {"id": e.id, "title": e.title}
        if e.date:
            out["date"] = e.date
        if e.note:
            out["note"] = e.note
        if not e.on_base:
            out["new"] = "not on the base resume yet"
        out["bullets"] = [
            {"id": b.id, "text": b.text + (f" ({b.sub})" if b.sub else "")}
            | (
                {"too_long": f"over 2 lines: shorten to {pool.max_chars} characters"}
                if len(b.text) > pool.max_chars
                else {}
            )
            for b in e.bullets
        ]
        tools = [line for line in e.context.splitlines() if line not in (e.title, e.note)]
        if tools:
            out["details"] = tools
        return out

    return {
        "summary": pool.summary,
        "experience": [entry(e) for e in pool.roles],
        "projects": [entry(e) for e in pool.projects],
        "skills": [{"label": label, "items": items} for label, items in pool.skill_lines],
        "more_confirmed_skills": pool.extra_skills,
    }
