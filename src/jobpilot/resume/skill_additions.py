"""Missing job skills the tailored resume adds.

A gap skill is added when the skills taxonomy knows it and it is either related to a skill your resume
already lists (a prerequisite or implied skill either way, or the same category) or quick to learn. "Quick to
learn" comes from the knowledge base when the gap item carries it (`easy`, matching/gap.py: learning material
available + closeness to your skills); older gap items fall back to taxonomy depth <= 2.
Soft skills and domains are never added. Code decides eligibility; the model only places skills on a line.

The score after the change is exact: each added skill is worth its gap points (scoring/gap.py), so
after = before + the points of every added skill, capped at 100.
"""

from __future__ import annotations

from typing import Any

from jobpilot.models.resume_doc import Layout, ResumeDoc
from jobpilot.reference import catalog

EASY_DEPTH = 2
NEVER_ADDED = {"soft", "domain"}


def _skill_lines(doc: ResumeDoc) -> list[Any]:
    """Return the visible entries of the visible skills sections."""
    return [
        e
        for s in doc.sections
        if s.layout == Layout.SKILLS and not s.hidden
        for e in s.entries
        if not e.hidden
    ]


def resume_skill_ids(doc: ResumeDoc) -> set[str]:
    """Return the taxonomy ids the resume's skills lines name, plus what they imply."""
    ids: set[str] = set()
    for entry in _skill_lines(doc):
        for item in entry.items:
            if (sid := catalog.skill_id(item)) is not None:
                ids.add(sid)
                ids.update(catalog.implied(sid))
    return ids


def why_addable(
    skill_name: str,
    skill_id: str | None,
    have: set[str],
    easy: bool | None = None,
    learn_reason: str | None = None,
) -> str | None:
    """Explain why a gap skill may be added to the resume.

    Args:
        skill_name: The skill as the JD names it.
        skill_id: Its taxonomy id, if known.
        have: Taxonomy ids the resume already has.
        easy: The knowledge base's quick-to-learn flag, if known.
        learn_reason: Why it is quick to learn.

    Returns:
        The reason, or None when it stays on the learn list.
    """
    sid = skill_id or catalog.skill_id(skill_name)
    skill = catalog.get(sid) if sid else None
    if skill is None or skill.category in NEVER_ADDED or sid in have:
        return None
    # Linked either way: a prerequisite or implied skill of one another.
    linked = (set(skill.prerequisites) | set(skill.implies)) & have
    for other in have:
        known = catalog.get(other)
        if known is not None and sid in (set(known.prerequisites) | set(known.implies)):
            linked.add(other)
    if linked:
        names = sorted((catalog.get(i).name if catalog.get(i) else i) for i in linked)  # type: ignore[union-attr]
        return "related to " + ", ".join(names[:2])
    quick = easy if easy is not None else skill.depth <= EASY_DEPTH
    if quick:
        return f"quick to learn ({learn_reason})" if learn_reason else "quick to learn"
    same = [i for i in have if (k := catalog.get(i)) is not None and k.category == skill.category]
    if same:
        return f"same area as {catalog.get(sorted(same)[0]).name}"  # type: ignore[union-attr]
    return None


def eligible(gap: list[dict[str, Any]], doc: ResumeDoc) -> list[dict[str, Any]]:
    """The gap items the resume may gain, each with its reason, highest points first."""
    have = resume_skill_ids(doc)
    out = []
    for item in gap:
        reason = why_addable(
            str(item.get("skill") or ""),
            item.get("skill_id"),
            have,
            item.get("easy"),
            item.get("learn_reason"),
        )
        if reason is not None:
            out.append({**item, "reason": reason})
    return out


def boost(operations: list[dict[str, Any]], gap: list[dict[str, Any]], score: float) -> dict[str, Any]:
    """The skills a version added (with points) and the job-fit score before and after them."""
    points = {str(g.get("skill") or "").lower(): float(g.get("points") or 0) for g in gap}
    added: list[dict[str, Any]] = [
        {"skill": str(op["skill"]), "points": points.get(str(op["skill"]).lower(), 0.0)}
        for op in operations
        if op.get("op") == "add_skill"
    ]
    after = min(100.0, round(score + sum(float(a["points"]) for a in added), 1))
    return {"added": added, "before": round(score, 1), "after": after}
