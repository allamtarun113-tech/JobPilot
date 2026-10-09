"""The skills taxonomy (Data/taxonomy/skills.json): normalize any skill name to a canonical skill id."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache

from jobpilot.core.config import REPO_ROOT

TAXONOMY = REPO_ROOT / "Data" / "taxonomy" / "skills.json"


@dataclass(frozen=True)
class Skill:
    """One taxonomy skill with its category, prerequisites and implications."""

    id: str
    name: str
    category: str
    prerequisites: tuple[str, ...]
    resources_expected: bool
    implies: tuple[str, ...] = ()
    depth: int = 0


def _norm(name: str) -> str:
    """Normalize a skill name: lower case, no parentheticals, single spaces."""
    name = re.sub(r"\(.*?\)", " ", name.lower())  # "Javascript (Basics)" -> "javascript"
    return re.sub(r"\s+", " ", name).strip(" .,")


@lru_cache
def _load() -> tuple[dict[str, Skill], dict[str, str]]:
    """Load the taxonomy once and build the alias -> skill id index.

    Returns:
        (skill id -> Skill, normalized alias -> skill id).
    """
    data = json.loads(TAXONOMY.read_text())
    skills: dict[str, Skill] = {}
    index: dict[str, str] = {}
    for s in data["skills"]:
        skills[s["id"]] = Skill(
            s["id"],
            s["name"],
            s["category"],
            tuple(s["prerequisites"]),
            s["resources_expected"],
            tuple(s.get("implies") or ()),
            int(s.get("depth") or 0),
        )
        for alias in (s["id"], s["name"], *s["aliases"]):
            index.setdefault(_norm(alias), s["id"])
            # Also index without spaces, so "Scikit Learn" finds
            # "scikitlearn".
            index.setdefault(_norm(alias).replace(" ", ""), s["id"])
    return skills, index


def skill_id(name: str) -> str | None:
    """Return the canonical id for a skill name, or None.

    Exact alias match only (after normalizing case, parentheticals and
    spaces); "A/B" also tries A and B. Never guesses.
    """
    _, index = _load()
    n = _norm(name)
    for candidate in (n, n.replace(" ", "")):
        if candidate in index:
            return index[candidate]
    if "/" in n:
        for part in n.split("/"):
            if (sid := index.get(part.strip())) is not None:
                return sid
    return None


def get(skill: str) -> Skill | None:
    """Return the Skill with this id, or None."""
    skills, _ = _load()
    return skills.get(skill)


def implied(skill: str) -> tuple[str, ...]:
    """Skills that having `skill` implies (e.g. fastapi -> python), from the taxonomy."""
    found = get(skill)
    return found.implies if found else ()


# How a skill typed in notes is written on a resume or in an email (lower-cased text -> display name).
DISPLAY = {
    "llm": "LLMs",
    "llms": "LLMs",
    "genai": "Generative AI",
    "gen ai": "Generative AI",
    "agentic ai": "Agentic AI",
    "multi agents": "Multi-agent systems",
    "multi agent": "Multi-agent systems",
}
_SHORT_UPPER = 3  # ALL-CAPS words this short stay upper case (AI, NLP, API)


def display_skill(text: str) -> str:
    """How a skill is written: your own capitals, except where the official name has inner capitals
    ('Numpy' -> 'NumPy', 'Lang Chain' -> 'LangChain', 'Javascript (Basics)' -> 'JavaScript (Basics)') or your
    text is all lower or all upper case ('GIT' -> 'Git'). 'Machine Learning' stays as you wrote it."""
    text = " ".join(re.sub(r"(\*\*|__|`)", "", text).split())
    lowered = text.lower()
    if lowered in DISPLAY:
        return DISPLAY[lowered]
    head, paren = re.match(r"^(.*?)(\s*\(.*\))?$", text).groups()  # type: ignore[union-attr]
    sid = skill_id(head)
    known = get(sid) if sid else None
    if known is not None and known.name.lower().replace(" ", "") == head.lower().replace(" ", ""):
        inner_caps = any(c.isupper() for c in known.name[1:].replace(" ", ""))
        badly_cased = head.islower() or head.isupper()
        if inner_caps or badly_cased:
            return known.name + (paren or "")
    return " ".join(p.capitalize() if p.isupper() and len(p) > _SHORT_UPPER else p for p in text.split())
