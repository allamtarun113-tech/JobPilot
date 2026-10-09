"""Deterministic gap-to-free-resource lookup from Data/learning only.

- Resources for a skill come from skill_resources.json in their stored (ranked) order; if a skill has none,
  its prerequisites' resources are used. Skills with resources_expected=false get no links.
- Only catalog URLs are ever shown - the LLM never writes links.
- A resource is hidden when its link check stops returning 200/401/403 (knowledge/learning.py).
- learning_plan() orders a multi-skill plan so prerequisites come first, then by taxonomy depth.
"""

from __future__ import annotations

from typing import Any

from jobpilot.knowledge.learning import data, link_ok, overrides
from jobpilot.reference import catalog


def for_skill(skill_id: str | None, limit: int = 2) -> list[dict[str, Any]]:
    """Return the first ``limit`` working free resources for a skill.

    Falls back to the prerequisites' resources when the skill has
    none; skills that expect no resources (soft skills, domains) get
    none.

    Args:
        skill_id: The taxonomy skill id.
        limit: How many resources to return.

    Returns:
        Catalog resources in their ranked order.
    """
    if not skill_id:
        return []
    skill = catalog.get(skill_id)
    if skill is not None and not skill.resources_expected:
        return []
    mapping, resources = data()
    ids = mapping.get(skill_id, [])
    if not ids and skill is not None:
        ids = [rid for prerequisite in skill.prerequisites for rid in mapping.get(prerequisite, [])]
    status = overrides()
    return [resources[rid] for rid in ids if rid in resources and link_ok(resources[rid], status)][:limit]


def learning_plan(skill_ids: list[str]) -> list[str]:
    """Order gap skills for study: prerequisites before the skills needing them, then shallow first."""
    wanted = [s for s in dict.fromkeys(skill_ids) if s]
    ordered: list[str] = []
    visiting: set[str] = set()

    def visit(sid: str) -> None:
        """Add a skill after its wanted prerequisites (depth-first)."""
        # ``visiting`` guards against prerequisite cycles in the
        # taxonomy.
        if sid in ordered or sid in visiting:
            return
        visiting.add(sid)
        skill = catalog.get(sid)
        for pre in sorted(skill.prerequisites if skill else (), key=_depth):
            if pre in wanted:
                visit(pre)
        visiting.discard(sid)
        ordered.append(sid)

    for sid in sorted(wanted, key=_depth):
        visit(sid)
    return ordered


def _depth(skill_id: str) -> int:
    """Return a skill's taxonomy depth (0 when unknown)."""
    skill = catalog.get(skill_id)
    return skill.depth if skill else 0
