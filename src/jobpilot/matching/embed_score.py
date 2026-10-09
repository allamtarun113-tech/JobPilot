"""Stage A: embedding similarity between the profile and each snippet - cheap, tuned for recall.

Profile item vectors live in Qdrant (rebuilt when the confirmed profile changes); job vectors are stored too.
"""

from __future__ import annotations

import logging
from typing import Any

from jobpilot.integrations.vectors import JOBS, PROFILE_ITEMS, VectorStore
from jobpilot.llm.embeddings import Embedder, cosine
from jobpilot.matching.profile_view import ProfileView

log = logging.getLogger(__name__)

SUMMARY_POINT = 0  # point id of the whole-profile vector in the profile_items collection
LAYOUT = 2  # 2: confirmed skills are indexed too (kind "skill"), for learnability


def job_text(job: dict[str, Any]) -> str:
    """Return the text embedded for a job: title, company and JD (max 2,000 chars)."""
    return f"{job['title']}. {job['company']}. {job.get('jd_text') or ''}"[:2000]


async def sync_profile(view: ProfileView, embedder: Embedder, vectors: VectorStore) -> list[float]:
    """(Re)index the confirmed profile in Qdrant if it changed; return the whole-profile vector."""
    current = await vectors.retrieve(PROFILE_ITEMS, SUMMARY_POINT)
    # Skip the rebuild while the profile and the index layout are
    # unchanged.
    if (
        current
        and current[1].get("profile_hash") == view.hash
        and current[1].get("layout") == LAYOUT
        and current[0]
    ):
        return current[0]
    ids = list(view.context_items)
    skill_ids = list(view.skill_texts)
    texts = [
        view.summary_text,
        *[view.context_items[i] for i in ids],
        *[view.skill_texts[i] for i in skill_ids],
    ]
    vecs = await embedder.embed(texts)
    points = [
        (SUMMARY_POINT, vecs[0], {"kind": "summary_vector", "profile_hash": view.hash, "layout": LAYOUT})
    ]
    points += [
        (item_id, vec, {"kind": "context", "text": view.context_items[item_id][:300]})
        for item_id, vec in zip(ids, vecs[1 : 1 + len(ids)], strict=True)
    ]
    points += [
        (item_id, vec, {"kind": "skill", "text": view.skill_texts[item_id][:120]})
        for item_id, vec in zip(skill_ids, vecs[1 + len(ids) :], strict=True)
    ]
    await vectors.replace_all(PROFILE_ITEMS, points)
    return vecs[0]


async def stage_a(
    jobs: list[dict[str, Any]], profile_vec: list[float], embedder: Embedder, vectors: VectorStore
) -> dict[int, float]:
    """Score jobs by embedding similarity to the whole profile (stage A).

    The job vectors are also stored in Qdrant.

    Args:
        jobs: Job rows to score.
        profile_vec: The whole-profile vector.
        embedder: Embeds the job texts.
        vectors: The Qdrant store.

    Returns:
        Job id -> cosine similarity.
    """
    if not jobs:
        return {}
    vecs = await embedder.embed([job_text(j) for j in jobs])
    try:  # stored for later use only; the scores below never depend on it, so a failure never stops a run
        await vectors.upsert(
            JOBS,
            [
                (j["id"], v, {"company": j["company_norm"], "title": j["title_norm"]})
                for j, v in zip(jobs, vecs, strict=True)
            ],
        )
    except Exception as exc:
        log.warning("job vectors not stored in Qdrant (%s); scoring continues", type(exc).__name__)
    return {j["id"]: round(cosine(profile_vec, v), 4) for j, v in zip(jobs, vecs, strict=True)}


async def best_context_match(text: str, embedder: Embedder, vectors: VectorStore) -> tuple[float, str | None]:
    """Best similarity between `text` and the user's projects, roles and achievements (domain part)."""
    if not text.strip():
        return 0.0, None
    (vec,) = await embedder.embed([text])
    hits = await vectors.search(PROFILE_ITEMS, vec, limit=1, must={"kind": "context"})
    return (hits[0][1], hits[0][2].get("text")) if hits else (0.0, None)
