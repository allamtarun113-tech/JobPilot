"""The free learning resources (Data/learning) - the catalog itself and its copy in Qdrant.

- Resources for a skill keep their stored (ranked) order from skill_resources.json; each Qdrant point carries
  that rank per skill, so a skill lookup needs no embedding and no model call.
- A resource is hidden when its link check stops returning 200/401/403. The shipped status lives in
  resources.json; `make check-links` writes fresher results to var/resource_link_status.json, which wins.
- Only catalog URLs are ever shown - the model never writes links.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jobpilot.core.config import REPO_ROOT
from jobpilot.reference import catalog

if TYPE_CHECKING:  # the dashboard reads the catalog here; it never loads the vector client
    from jobpilot.integrations.vectors import VectorStore
    from jobpilot.llm.embeddings import Embedder

LINK_STATUS_OVERRIDES = REPO_ROOT / "var" / "resource_link_status.json"
OK_STATUSES = (200, 401, 403)
EMBED_BATCH = 128


@lru_cache
def data() -> tuple[dict[str, list[str]], dict[str, dict[str, Any]]]:
    """(skill_id -> ranked resource ids, resource id -> resource)."""
    mapping = json.loads((REPO_ROOT / "Data/learning/skill_resources.json").read_text())["skills"]
    resources = json.loads((REPO_ROOT / "Data/learning/resources.json").read_text())["resources"]
    return ({key: value["resource_ids"] for key, value in mapping.items()}, {r["id"]: r for r in resources})


def all_resources() -> dict[str, dict[str, Any]]:
    """Return every catalog resource, keyed by resource id."""
    return data()[1]


def overrides(path: Path = LINK_STATUS_OVERRIDES) -> dict[str, int]:
    """Return fresher link-check statuses from ``make check-links``, if any."""
    try:
        return {k: int(v) for k, v in json.loads(path.read_text()).items()}
    except (OSError, ValueError):
        return {}


def link_ok(resource: dict[str, Any], status_overrides: dict[str, int] | None = None) -> bool:
    """Return True if a resource's link still answers 200, 401 or 403.

    Args:
        resource: A catalog resource or a Qdrant payload.
        status_overrides: Fresher statuses; read from disk when None.
    """
    found = status_overrides if status_overrides is not None else overrides()
    status = found.get(resource.get("id") or resource.get("resource_id") or "", resource.get("link_status"))
    return status in OK_STATUSES


def point_id(resource_id: str) -> int:
    """Return a stable 63-bit Qdrant point id for a resource id."""
    return int.from_bytes(hashlib.sha256(f"resource:{resource_id}".encode()).digest()[:8], "big") & (
        (1 << 63) - 1
    )


def points_payload() -> list[tuple[str, dict[str, Any], str]]:
    """(resource id, payload, text to embed) for every resource, tagged with every skill it serves."""
    mapping, resources = data()
    ranks: dict[str, dict[str, int]] = {}
    # Remember each resource's rank within every skill it serves.
    for skill_id, ids in mapping.items():
        for rank, rid in enumerate(ids):
            ranks.setdefault(rid, {})[skill_id] = rank
    out = []
    for rid, r in resources.items():
        skills = list(dict.fromkeys([*(r.get("skill_ids") or []), *ranks.get(rid, {})]))
        names = [s.name for sid in skills if (s := catalog.get(sid)) is not None]
        payload = {
            "resource_id": rid,
            "title": r["title"],
            "provider": r.get("provider"),
            "url": r["url"],
            "media": r.get("media"),
            "kind": r.get("kind"),
            "level": r.get("level"),
            "est_hours": r.get("est_hours"),
            "free": r.get("free"),
            "primary_skill_id": r.get("primary_skill_id"),
            "skill_ids": skills,
            "rank": ranks.get(rid, {}),
            "link_status": r.get("link_status"),
            "description": r.get("description") or "",
        }
        text = (
            f"{r['title']}. {r.get('description') or ''} Skills: {', '.join(names)}. "
            f"{r.get('kind')} by {r.get('provider')}"
        )
        out.append((rid, payload, text))
    return out


async def ingest(vectors: VectorStore, embedder: Embedder) -> int:
    """Rebuild the learning_resources collection from Data/learning (no model calls; local embeddings)."""
    from jobpilot.integrations.vectors import LEARNING_RESOURCES

    rows = points_payload()
    points: list[tuple[int, list[float], dict[str, Any]]] = []
    for start in range(0, len(rows), EMBED_BATCH):
        batch = rows[start : start + EMBED_BATCH]
        vectors_ = await embedder.embed([text for _rid, _payload, text in batch])
        points += [
            (point_id(rid), vec, payload) for (rid, payload, _t), vec in zip(batch, vectors_, strict=True)
        ]
    await vectors.replace_all(LEARNING_RESOURCES, points)
    return len(points)
