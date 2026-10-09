"""Qdrant: every vector JobPilot keeps.

Qdrant Cloud when QDRANT_URL is set, otherwise embedded local mode in var/qdrant (only the worker opens it;
local mode allows one process at a time).

Job and profile vectors are derived data and can be rebuilt from Postgres; books and learning resources are
rebuilt from Data/ (make ingest-corpus, make ingest-learning). Skill notes and the Q&A bank are written by the
model and live only here (decision 28): rebuilding them costs model calls (make skill-notes).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException

from jobpilot.core.config import REPO_ROOT, get_config, get_settings

PROFILE_ITEMS = "profile_items"
JOBS = "jobs"
PREP_CHUNKS = "prep_chunks"  # book sections (Data/books)
LEARNING_RESOURCES = "learning_resources"  # free courses, videos, docs (Data/learning)
SKILL_NOTES = "skill_notes"  # one simple-English note per skill
QA_BANK = "qa_bank"  # interview questions and answers, reused across jobs

UPSERT_BATCH = 64  # small requests: a slow uplink timed out on 103 job vectors in one request (run 18)
UPSERT_TRIES = 3
log = logging.getLogger(__name__)

# Payload fields that are filtered on (Qdrant Cloud strict mode refuses filters on unindexed fields).
KEYWORD_INDEXES = {
    PROFILE_ITEMS: ("kind",),
    PREP_CHUNKS: ("skill_ids",),
    LEARNING_RESOURCES: ("skill_ids", "level"),
    SKILL_NOTES: ("skill_id",),
    QA_BANK: ("skill_ids", "category", "source"),
}


class VectorStore:
    """A thin async wrapper over one Qdrant client."""

    def __init__(self, client: AsyncQdrantClient, dim: int) -> None:
        """Wrap ``client``; every collection uses ``dim``-sized vectors."""
        self.client = client
        self.dim = dim
        self._ready: set[str] = set()

    @classmethod
    def from_settings(cls) -> VectorStore:
        """Connect to Qdrant Cloud when QDRANT_URL is set, else local mode.

        Raises:
            RuntimeError: On a serverless host without QDRANT_URL.
        """
        s = get_settings()
        dim = get_config().embeddings.dim
        if s.qdrant_url:
            key = s.qdrant_api_key.get_secret_value() if s.qdrant_api_key else None
            timeout = get_config().vectors.timeout_seconds
            return cls(AsyncQdrantClient(url=s.qdrant_url, api_key=key, timeout=timeout), dim)
        if s.serverless:
            raise RuntimeError("set QDRANT_URL on a serverless host")
        return cls(AsyncQdrantClient(path=str(REPO_ROOT / get_config().vectors.local_path)), dim)

    @classmethod
    def in_memory(cls, dim: int = 384) -> VectorStore:
        """Return a throwaway in-memory store (tests)."""
        return cls(AsyncQdrantClient(location=":memory:"), dim)

    async def ensure(self, collection: str) -> None:
        """Create a collection and its payload indexes if missing (once per process)."""
        if collection in self._ready:
            return
        if not await self.client.collection_exists(collection):
            await self.client.create_collection(
                collection, vectors_config=models.VectorParams(size=self.dim, distance=models.Distance.COSINE)
            )
        for field in KEYWORD_INDEXES.get(collection, ()):
            await self.client.create_payload_index(
                collection, field_name=field, field_schema=models.PayloadSchemaType.KEYWORD, wait=True
            )
        self._ready.add(collection)

    async def upsert(self, collection: str, points: list[tuple[int, list[float], dict[str, Any]]]) -> None:
        """Insert or replace points (id, vector, payload) in batches."""
        if not points:
            return
        await self.ensure(collection)
        for start in range(0, len(points), UPSERT_BATCH):  # keeps each request small
            batch = [
                models.PointStruct(id=pid, vector=vec, payload=payload)
                for pid, vec, payload in points[start : start + UPSERT_BATCH]
            ]
            for attempt in range(1, UPSERT_TRIES + 1):  # a network hiccup is retried, with a short wait
                try:
                    await self.client.upsert(collection, points=batch)
                    break
                except (ResponseHandlingException, httpx.TransportError) as exc:
                    if attempt == UPSERT_TRIES:
                        raise
                    log.warning("qdrant upsert to %s failed (%s); retrying", collection, type(exc).__name__)
                    await asyncio.sleep(attempt)

    async def replace_all(
        self, collection: str, points: list[tuple[int, list[float], dict[str, Any]]]
    ) -> None:
        """Drop a collection and fill it again with ``points``."""
        if await self.client.collection_exists(collection):
            await self.client.delete_collection(collection)
        self._ready.discard(collection)
        await self.ensure(collection)
        await self.upsert(collection, points)

    @staticmethod
    def _filter(must: dict[str, Any] | None) -> models.Filter | None:
        """Every key must match; on a list field, a match on any element counts."""
        if not must:
            return None
        return models.Filter(
            must=[models.FieldCondition(key=k, match=models.MatchValue(value=v)) for k, v in must.items()]
        )

    async def search(
        self, collection: str, vector: list[float], limit: int = 5, must: dict[str, Any] | None = None
    ) -> list[tuple[int, float, dict[str, Any]]]:
        """Return the ``limit`` nearest points as (id, score, payload).

        Args:
            collection: The collection to search.
            vector: The query vector.
            limit: How many points to return.
            must: Payload fields that must match exactly.
        """
        await self.ensure(collection)
        flt = self._filter(must)
        result = await self.client.query_points(
            collection, query=vector, limit=limit, query_filter=flt, with_payload=True
        )
        return [(int(p.id), float(p.score), dict(p.payload or {})) for p in result.points]

    async def retrieve(self, collection: str, point_id: int) -> tuple[list[float], dict[str, Any]] | None:
        """Return one point's vector and payload, or None."""
        await self.ensure(collection)
        points = await self.client.retrieve(collection, ids=[point_id], with_vectors=True, with_payload=True)
        if not points:
            return None
        vector = points[0].vector
        return (list(vector) if isinstance(vector, list) else [], dict(points[0].payload or {}))  # type: ignore[arg-type]

    async def scroll(
        self, collection: str, must: dict[str, Any], limit: int = 50
    ) -> list[tuple[int, dict[str, Any]]]:
        """Points matching a payload filter, without a query vector (no embedding needed)."""
        await self.ensure(collection)
        points, _next = await self.client.scroll(
            collection, scroll_filter=self._filter(must), limit=limit, with_payload=True, with_vectors=False
        )
        return [(int(p.id), dict(p.payload or {})) for p in points]

    async def count(self, collection: str, must: dict[str, Any] | None = None) -> int:
        """Return how many points match the payload filter."""
        await self.ensure(collection)
        return (await self.client.count(collection, count_filter=self._filter(must), exact=True)).count

    async def close(self) -> None:
        """Close the Qdrant client."""
        await self.client.close()
