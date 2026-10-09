"""The confirmed profile as scoring needs it.

Skills (by taxonomy id and by name, plus what they imply), years of
experience, role titles, and the item texts that get embedded for the
domain and stage A similarity.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core import clock
from jobpilot.db.queries import profile_items
from jobpilot.models.profile import ENTRY_FIELDS, fact_texts, tech_list
from jobpilot.reference import catalog

MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}


def _parse_month(text: str) -> date | None:
    """Parse "Jun 2024", "2024" or "present" into a date (None if unknown)."""
    text = text.strip().lower()
    if text in ("present", "now", "current", "till date", "today"):
        return clock.now().date()
    m = re.match(r"([a-z]{3})[a-z]*\.?\s+(\d{4})", text)
    if m and m.group(1) in MONTHS:
        return date(int(m.group(2)), MONTHS[m.group(1)], 1)
    if m := re.match(r"(\d{4})$", text):
        return date(int(m.group(1)), 1, 1)
    return None


def months_between(dates: str | None) -> int:
    """'june 2024–present' -> months elapsed (0 if unparseable)."""
    if not dates:
        return 0
    parts = re.split(r"\s*[–—-]\s*|\s+to\s+", dates)
    if len(parts) != 2:
        return 0
    start, end = _parse_month(parts[0]), _parse_month(parts[1])
    if not start or not end or end < start:
        return 0
    return (end.year - start.year) * 12 + end.month - start.month + 1


@dataclass
class ProfileView:
    """The confirmed profile, indexed the ways scoring reads it."""

    skill_ids: dict[str, list[int]] = field(default_factory=dict)  # taxonomy id -> item ids (incl. implied)
    skill_names: dict[str, list[int]] = field(default_factory=dict)  # lower-cased name -> item ids
    skill_texts: dict[int, str] = field(default_factory=dict)  # item id -> skill name (for similarity)
    years: float = 0.0
    role_titles: list[str] = field(default_factory=list)
    context_items: dict[int, str] = field(
        default_factory=dict
    )  # project / role / achievement / summary texts
    summary_text: str = ""
    hash: str = ""
    confirmed_count: int = 0


async def load(conn: AsyncConnection) -> ProfileView:
    """Build the scoring view of the confirmed profile.

    Args:
        conn: An open connection.

    Returns:
        The view, with a hash that changes whenever any confirmed item
        changes.
    """
    rows = await profile_items.list_all(conn, statuses=("confirmed",))
    view = ProfileView(confirmed_count=len(rows))
    months = 0
    digest = hashlib.sha256()
    skills: list[str] = []
    facts = fact_texts(rows)  # experiences / projects with their fields; details with where they belong

    def add_skill(name: str, item_id: int, sid: str | None) -> None:
        """Index a skill under its name, its taxonomy id and every skill it implies."""
        view.skill_names.setdefault(name.lower(), []).append(item_id)
        if sid:
            view.skill_ids.setdefault(sid, []).append(item_id)
            for implied in catalog.implied(sid) if catalog.get(sid) else ():
                view.skill_ids.setdefault(implied, []).append(item_id)

    for r in rows:
        # Hash ids and versions: any edit changes the profile hash.
        digest.update(f"{r['id']}:{r['version']};".encode())
        kind, text, attrs = r["kind"], r["text"], r["attrs"] or {}
        if kind == "skill":
            skills.append(text)
            view.skill_texts[r["id"]] = text
            add_skill(text, r["id"], attrs.get("skill_id") or catalog.skill_id(text))
            continue
        if kind in ENTRY_FIELDS:  # the tools an experience / project lists are skills it is evidence for
            for tech in tech_list(attrs):
                if tech.lower() not in view.skill_names:
                    skills.append(tech)
                add_skill(tech, r["id"], catalog.skill_id(tech))
        if kind == "role":
            title = attrs.get("title") or text
            view.role_titles.append(str(title))
            # Internships don't count toward years of experience.
            if (
                "intern" not in str(title).lower()
                and "intern" not in str(attrs.get("employment_type", "")).lower()
            ):
                months += months_between(attrs.get("dates"))
        if kind in ("role", "project", "achievement", "metric", "activity", "summary"):
            view.context_items[r["id"]] = facts[r["id"]]
    view.years = round(months / 12, 1)
    view.hash = digest.hexdigest()[:16]
    view.summary_text = " ".join(
        [
            f"Roles: {', '.join(view.role_titles)}.",
            f"Skills: {', '.join(skills)}.",
            *view.context_items.values(),
        ]
    )[:4000]
    return view
