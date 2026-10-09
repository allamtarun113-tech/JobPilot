"""Retrieve book sections (prep_chunks) for prep topics."""

from __future__ import annotations

from typing import Any

from jobpilot.integrations.vectors import PREP_CHUNKS, VectorStore
from jobpilot.llm.embeddings import Embedder
from jobpilot.models.prep import Topic


async def for_topics(
    vectors: VectorStore, embedder: Embedder, topics: list[Topic], per_topic: int = 4
) -> list[dict[str, Any]]:
    """Return the best book sections for each topic, without repeats.

    Sections tagged with one of the topic's skills rank first, then by
    similarity.

    Args:
        vectors: The Qdrant store.
        embedder: Embeds the topic queries.
        topics: The prep topics.
        per_topic: Sections to keep per topic.

    Returns:
        Section payloads with a ``chunk:<id>`` citation and a score.
    """
    queries = [f"{topic.name} {topic.reason} {' '.join(topic.skill_ids)}" for topic in topics]
    embeddings = await embedder.embed(queries)
    selected: dict[int, dict[str, Any]] = {}
    for topic, vector in zip(topics, embeddings, strict=True):
        rows = await vectors.search(PREP_CHUNKS, vector, limit=per_topic * 2)
        ranked = sorted(
            rows,
            key=lambda row: (
                bool(set(topic.skill_ids) & set(row[2].get("skill_ids", []))),
                row[1],
            ),
            reverse=True,
        )[:per_topic]
        for point_id, score, payload in ranked:
            selected[point_id] = {"citation": f"chunk:{point_id}", "score": score, **payload}
    return list(selected.values())
