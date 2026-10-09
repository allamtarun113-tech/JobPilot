"""Everything the knowledge base holds about one skill.

Its note, its learning resources (in catalog rank order, broken links
hidden) and its interview Q&A. Payload filters only - no embedding and
no model call - and the three reads run at once, so a lookup takes one
round trip to Qdrant.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from jobpilot.integrations.vectors import LEARNING_RESOURCES, PREP_CHUNKS, QA_BANK, SKILL_NOTES, VectorStore
from jobpilot.knowledge.learning import link_ok, overrides


@dataclass
class SkillKnowledge:
    """What the knowledge base holds about one skill."""

    skill_id: str
    note: dict[str, Any] | None = None
    resources: list[dict[str, Any]] = field(default_factory=list)
    qa: list[dict[str, Any]] = field(default_factory=list)


async def for_skill(
    vectors: VectorStore, skill_id: str, *, resources: int = 20, qa: int = 30
) -> SkillKnowledge:
    """Look up a skill's note, resources and Q&A in one round trip.

    Args:
        vectors: The Qdrant store.
        skill_id: The taxonomy skill id.
        resources: The most resources to return (0 skips the read).
        qa: The most Q&A items to return (0 skips the read).

    Returns:
        The skill's knowledge; missing parts are empty.
    """

    async def nothing() -> list[tuple[int, dict[str, Any]]]:
        """Stand in for a skipped read."""
        return []

    notes, found, questions = await asyncio.gather(  # a count of 0 skips that read
        vectors.scroll(SKILL_NOTES, {"skill_id": skill_id}, limit=1),
        vectors.scroll(LEARNING_RESOURCES, {"skill_ids": skill_id}, limit=200) if resources else nothing(),
        vectors.scroll(QA_BANK, {"skill_ids": skill_id}, limit=qa) if qa else nothing(),
    )
    status = overrides()
    # Catalog rank for this skill first; a resource whose main skill
    # is this one wins a tie.
    ranked = sorted(
        (payload for _id, payload in found if link_ok(payload, status)),
        key=lambda p: (p.get("rank", {}).get(skill_id, 10_000), p.get("primary_skill_id") != skill_id),
    )
    return SkillKnowledge(
        skill_id=skill_id,
        note=notes[0][1] if notes else None,
        resources=ranked[:resources],
        qa=[payload for _id, payload in questions],
    )


async def material(vectors: VectorStore, skill_id: str) -> tuple[int, int]:
    """(learning resources, book sections) tagged with this skill."""
    resources, sections = await asyncio.gather(
        vectors.count(LEARNING_RESOURCES, {"skill_ids": skill_id}),
        vectors.count(PREP_CHUNKS, {"skill_ids": skill_id}),
    )
    return resources, sections
