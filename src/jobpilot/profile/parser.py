"""Notes -> draft profile items.

One structured LLM call for facts that never made it onto the page.
(The resume's own items come from resume/profile_drafts.py.) A project
or job described in the notes becomes one entry with its details filed
under it, and a skill already in the profile is not added again.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import SequenceMatcher

from jobpilot.llm import prompts
from jobpilot.llm.client import LLMClient
from jobpilot.models.profile import DraftItem, ExtractedItem, ExtractedProfile, ItemKind, entry_headline
from jobpilot.reference import catalog
from jobpilot.safety.sanitize import fence

ENTRY_KINDS = (ItemKind.PROJECT, ItemKind.ROLE)
DETAIL_KINDS = (ItemKind.ACHIEVEMENT, ItemKind.METRIC)


def skill_key(name: str) -> str:
    """Compare form of a skill name: "Lang Chain", "langchain" and "LangChain (basics)" are one."""
    return re.sub(r"[\s_-]+", "", re.sub(r"\(.*?\)", " ", name.lower())).strip(" .,")


def same_skill(a: str, b: str) -> bool:
    """True if two skill names are the same skill, allowing a one-letter typo in longer names.

    Short names must match exactly (C vs C++, Java vs Jira), and the
    catalog id alone is not enough (Qdrant and Vector Database share one).
    """
    ka, kb = skill_key(a), skill_key(b)
    if ka == kb:
        return True
    return min(len(ka), len(kb)) >= 7 and SequenceMatcher(None, ka, kb).ratio() >= 0.9


def _fields(item: ExtractedItem) -> dict[str, str | int | list[str] | None]:
    """The structured fields of a project or role from the notes."""
    name = " ".join(item.text.split())
    return {
        "title" if item.kind == ItemKind.ROLE else "name": name,
        "about": item.about.strip(),
        "tech": [t.strip() for t in item.tech if t.strip()],
    }


def _evidence(item: ExtractedItem) -> str:
    """Where a notes item came from: the quote it rests on."""
    return f'Notes: "{item.evidence_quote}"'


async def extract_from_notes(
    llm: LLMClient,
    notes: str,
    places: dict[str, int] | None = None,
    known_skills: Iterable[str] = (),
) -> list[DraftItem]:
    """Turn the user's free-form notes into draft items.

    One structured call. Each item carries the quote it rests on, and
    is dropped if that quote isn't really in the notes. Projects and
    jobs come first so their details can point at them.

    Args:
        llm: The model client.
        notes: The user's notes.
        places: Experience / project name -> item id, so a detail can
            be filed under the one it is about.
        known_skills: Skill names already in the profile; these are not
            added again.

    Returns:
        The draft items.
    """
    places = places or {}
    known = list(known_skills)
    prompt = prompts.render(
        "profile/extract_items", notes=fence("notes", notes), places=list(places), skills=known
    )
    result = await llm.structured("profile_extract", prompt, ExtractedProfile)
    found = [
        item
        for item in result.output.items
        # the quote must really be in the notes: never accept invented facts
        if not item.evidence_quote.strip() or item.evidence_quote.strip().lower() in notes.lower()
    ]
    places_lower = {name.lower(): pid for name, pid in places.items()}
    entries: list[DraftItem] = []
    new_entries: dict[str, str] = {}  # lower-cased name -> import key
    # First pass: projects and jobs, so a detail listed before its project still finds it.
    for n, item in enumerate(found, 1):
        name = " ".join(item.text.split()).lower()
        if item.kind not in ENTRY_KINDS or name in places_lower or name in new_entries:
            continue  # already in the profile, or said twice: its details go under the one we have
        fields = _fields(item)
        new_entries[name] = f"notes:{n}"
        text = entry_headline(item.kind.value, {k: str(v) for k, v in fields.items() if k != "tech"})
        entries.append(
            DraftItem(key=f"notes:{n}", kind=item.kind, text=text, evidence=_evidence(item), attrs=fields)
        )
    others: list[DraftItem] = []
    seen_skills = list(known)
    for n, item in enumerate(found, 1):
        if item.kind in ENTRY_KINDS:
            continue
        attrs: dict[str, str | int | list[str] | None] = {}
        if item.kind == ItemKind.SKILL:
            if any(same_skill(item.text, s) for s in seen_skills):
                continue  # already in the profile (or earlier in these notes)
            seen_skills.append(item.text)
            attrs["skill_id"] = catalog.skill_id(item.text)
        if item.kind in DETAIL_KINDS:
            parent = item.belongs_to.strip().lower()  # only a listed or new name; never a guess
            if parent in places_lower:
                attrs["parent_id"] = places_lower[parent]
            elif parent in new_entries:
                attrs["parent"] = new_entries[parent]  # resolved by import key, like the resume importer
        others.append(
            DraftItem(key=f"notes:{n}", kind=item.kind, text=item.text, evidence=_evidence(item), attrs=attrs)
        )
    return [*entries, *others]
