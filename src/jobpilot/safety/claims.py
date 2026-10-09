"""Claim checks beyond numbers.

Generated text may not introduce a skill or a title/seniority word that
the cited profile facts (or the text it replaces) do not contain.
Skills come from Data/taxonomy/skills.json.

Matching is whole-word ("go" in "good" is not Go; C++ / C# / .NET
match). Aliases of two letters or fewer ("Go", "R", "C", "AI") match
only with the taxonomy's own capitalisation, so ordinary words don't
trigger.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

from jobpilot.core.config import REPO_ROOT

TAXONOMY = REPO_ROOT / "Data" / "taxonomy" / "skills.json"
TITLE_WORDS = (
    "intern",
    "junior",
    "senior",
    "lead",
    "principal",
    "staff",
    "manager",
    "head",
    "director",
    "architect",
    "chief",
    "vp",
    "founder",
    "co-founder",
    "cto",
    "ceo",
)
# Word edges that treat +, # and . as part of a name (C++, C#, .NET).
_EDGE_L, _EDGE_R = r"(?<![\w+#.])", r"(?![\w+#])"


@lru_cache
def _patterns() -> tuple[re.Pattern[str], re.Pattern[str], dict[str, str], dict[str, str]]:
    """Build the skill-matching regexes from the taxonomy (cached).

    Returns:
        A case-insensitive pattern for longer aliases, a case-sensitive
        pattern for short ones, and the two alias -> skill id maps.
    """
    data = json.loads(TAXONOMY.read_text())
    folded: dict[str, str] = {}  # lower-cased alias -> skill id (case-insensitive matching)
    exact: dict[str, str] = {}  # short alias as written -> skill id (case-sensitive matching)
    for skill in data["skills"]:
        for alias in (skill["name"], *skill.get("aliases", [])):
            alias = alias.strip()
            if not alias:
                continue
            if len(alias) <= 2:
                exact.setdefault(alias, skill["id"])
            else:
                folded.setdefault(alias.lower(), skill["id"])

    def alternation(terms: list[str]) -> str:
        """Join terms into a regex alternation, longest first.

        Longest first, so "Spring Boot" wins over "Spring".
        """
        return "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))

    loose = re.compile(f"{_EDGE_L}(?:{alternation(list(folded))}){_EDGE_R}", re.I)
    strict = re.compile(f"{_EDGE_L}(?:{alternation(list(exact))}){_EDGE_R}")
    return loose, strict, folded, exact


def skills_in(text: str) -> dict[str, str]:
    """skill id -> the words that named it, for every taxonomy skill mentioned in the text."""
    loose, strict, folded, exact = _patterns()
    found: dict[str, str] = {}
    for m in loose.finditer(text):
        found.setdefault(folded[m.group(0).lower()], m.group(0))
    for m in strict.finditer(text):
        found.setdefault(exact[m.group(0)], m.group(0))
    return found


def title_words_in(text: str) -> set[str]:
    """Return the seniority and title words (senior, lead, ...) in ``text``."""
    lowered = text.lower()
    return {w for w in TITLE_WORDS if re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", lowered)}


def backing_items(text: str, allowed_texts: list[str], items: dict[int, str]) -> list[int]:
    """Find confirmed items that back skills and titles ``text`` adds.

    A rewrite that names a skill the profile holds (say, a confirmed
    'C++' skill item) while citing only a project item is true - the
    model just cited too little. These ids are added as citations so
    every claim still rests on a cited confirmed item. Unbacked claims
    stay unsupported.

    Args:
        text: The generated text.
        allowed_texts: Texts already cited (or replaced).
        items: Confirmed item id -> fact text.

    Returns:
        Item ids to add as citations.
    """
    allowed = " \n ".join(allowed_texts)
    missing_skills = set(skills_in(text)) - set(skills_in(allowed))
    missing_titles = title_words_in(text) - title_words_in(allowed)
    found: list[int] = []
    # Shortest item first: a skill item ('C++') is a cleaner citation than a long project text.
    for item_id, item_text in sorted(items.items(), key=lambda kv: (len(kv[1]), kv[0])):
        hits = (missing_skills & set(skills_in(item_text))) | (missing_titles & title_words_in(item_text))
        if hits:
            found.append(item_id)
            missing_skills -= hits
            missing_titles -= hits
        if not missing_skills and not missing_titles:
            break
    return found


def claimable_skills(items: dict[int, str], added: tuple[str, ...] = ()) -> list[str]:
    """List every skill the checks accept for this profile.

    The confirmed profile's skills plus ``added``, so a writer can be
    told the full list instead of guessing.

    Args:
        items: Confirmed item id -> fact text.
        added: Extra skill names allowed for this job.

    Returns:
        Skill names, sorted case-insensitively.
    """
    names: dict[str, str] = {}
    for text in items.values():
        for sid, name in skills_in(text).items():
            names.setdefault(sid, name)
    return sorted({*names.values(), *added}, key=str.lower)


def unsupported(text: str, allowed_texts: list[str]) -> list[str]:
    """Skills and title words in `text` that none of `allowed_texts` contains (empty list = supported)."""
    allowed = " \n ".join(allowed_texts)
    known_skills = skills_in(allowed)
    known_titles = title_words_in(allowed)
    problems = [f"skill '{name}'" for sid, name in skills_in(text).items() if sid not in known_skills]
    problems += [f"title word '{w}'" for w in sorted(title_words_in(text) - known_titles)]
    return problems
