"""Gap to 100%: each missing item is worth exactly its share of its dimension.

5 required technologies and Kafka missing -> Kafka is worth 25 / 5 = 5 points. The list is ranked by points
weighted by learnability (`rank`), so quick wins come first among items worth about the same.

Learnability (decision 28) says how easy each missing skill is to pick up, from the Qdrant knowledge base and
no model call: how much learning material exists for it (resources + book sections), and how close it is to a
skill you already have (vector similarity, or a taxonomy link).
"""

from __future__ import annotations

import asyncio
import logging

from jobpilot.core.config import load_yaml
from jobpilot.integrations.vectors import PROFILE_ITEMS, VectorStore
from jobpilot.knowledge import lookup
from jobpilot.llm.embeddings import Embedder
from jobpilot.models.score import GapItem
from jobpilot.reference import catalog

log = logging.getLogger(__name__)
_material: dict[str, tuple[int, int]] = {}  # skill id -> (resources, sections); the catalog rarely changes
_nearest: dict[tuple[str, int], tuple[float, str]] = {}  # (skill, your skills) -> (similarity, nearest skill)


KIND = {"technologies": "technology", "skills": "skill", "concepts": "concept", "nice": "nice"}


def gap(items: list[tuple[str, str, float]]) -> list[GapItem]:
    """Turn missing items into gap items with their points.

    An item's points are its share of its dimension's (effective)
    weight, less any partial credit it earned.

    Args:
        items: (dimension, item, points still missing) tuples.

    Returns:
        Gap items worth more than 0, highest points first.
    """
    merged: dict[str, GapItem] = {}
    for dim, skill, points in items:
        item = GapItem(skill=skill, skill_id=catalog.skill_id(skill), kind=KIND[dim], points=points)
        key = item.skill_id or item.skill.lower()
        if key in merged:  # "JS" and "JavaScript" in one JD are one gap worth both shares
            merged[key] = merged[key].model_copy(
                update={"points": round(merged[key].points + item.points, 2)}
            )
        else:
            merged[key] = item
    return sorted((g for g in merged.values() if g.points > 0), key=lambda g: -g.points)


def rank(items: list[GapItem]) -> list[GapItem]:
    """Skills to learn, best first: points weighted by learnability (quick wins first among equals)."""
    base = _cfg()["rank_base"]

    def priority(g: GapItem) -> float:
        """Return points scaled by learnability (an unknown counts as 0.5)."""
        learn = g.learnability if g.learnability is not None else 0.5
        return g.points * (base + (1 - base) * learn)

    return sorted(items, key=lambda g: (-priority(g), -g.points, g.skill.lower()))


def _cfg() -> dict[str, float]:
    """Return the learnability settings from ``scoring.yaml``."""
    return {k: float(v) for k, v in load_yaml("scoring.yaml")["learnability"].items()}


def _linked(skill_id: str | None, have: set[str]) -> str | None:
    """A skill you have that is a prerequisite of, implied by, or implies this one."""
    skill = catalog.get(skill_id) if skill_id else None
    if skill is None:
        return None
    for other in sorted(have):
        known = catalog.get(other)
        if other in (*skill.prerequisites, *skill.implies) or (
            known is not None and skill_id in (*known.prerequisites, *known.implies)
        ):
            return known.name if known else other
    return None


async def learnability(item: GapItem, have: set[str], embedder: Embedder, vectors: VectorStore) -> GapItem:
    """Rate how easy one missing skill is to learn (0-1), with no model call.

    Two parts: how much free material exists for it (resources and book
    sections) and how close it is to a skill you have (vector
    similarity, or a taxonomy link).

    Args:
        item: The gap item.
        have: Taxonomy ids of the skills you have.
        embedder: Embeds the skill name.
        vectors: Qdrant (knowledge base and your skill vectors).

    Returns:
        A copy of the item with learnability, easy and the reason.
    """
    cfg = _cfg()
    resources = sections = 0
    if item.skill_id:
        if item.skill_id not in _material:
            _material[item.skill_id] = await lookup.material(vectors, item.skill_id)
        resources, sections = _material[item.skill_id]
    # Resources count a little more than book sections.
    material = 0.6 * min(1.0, resources / cfg["resources_full"]) + 0.4 * min(
        1.0, sections / cfg["sections_full"]
    )
    skill = catalog.get(item.skill_id) if item.skill_id else None
    name = skill.name if skill else item.skill
    key = (name.lower(), hash(frozenset(have)))  # many jobs in a run miss the same skills
    if key not in _nearest:
        [vector] = await embedder.embed([name])
        hits = await vectors.search(PROFILE_ITEMS, vector, limit=1, must={"kind": "skill"})
        _nearest[key] = (hits[0][1], str(hits[0][2].get("text") or "")) if hits else (0.0, "")
    similarity, nearest = _nearest[key]
    low, high = cfg["closeness_low"], cfg["closeness_high"]
    closeness = min(1.0, max(0.0, (similarity - low) / (high - low)))
    if linked := _linked(item.skill_id, have):
        closeness, nearest = max(closeness, cfg["close_linked"]), linked
    score = round(cfg["material_weight"] * material + cfg["closeness_weight"] * closeness, 2)
    parts = [f"{resources} free resources, {sections} book sections"]
    if closeness >= 0.5 and nearest:
        parts.append(f"close to your {nearest}")
    return item.model_copy(
        update={"learnability": score, "easy": score >= cfg["easy_at"], "learn_reason": "; ".join(parts)}
    )


async def with_learnability(
    items: list[GapItem], have: set[str], embedder: Embedder, vectors: VectorStore
) -> list[GapItem]:
    """The gap list with learnability on each item; unchanged (no learnability) if Qdrant can't be reached."""
    try:
        return rank(list(await asyncio.gather(*(learnability(i, have, embedder, vectors) for i in items))))
    except Exception as exc:  # the score must not depend on the knowledge base being up
        log.warning("learnability unavailable: %s", exc)
        return items
