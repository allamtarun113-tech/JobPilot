"""Run 18: a Qdrant Cloud upload timed out and failed the run. Uploads retry; job vectors are optional."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from jobpilot.integrations import vectors as vectors_mod
from jobpilot.integrations.vectors import VectorStore
from jobpilot.llm.embeddings import HashEmbedder
from jobpilot.matching import embed_score


class FlakyClient:
    """A Qdrant client whose upserts fail `fails` times, then succeed; records batch sizes."""

    def __init__(self, fails: int) -> None:
        """Fail this many upserts first."""
        self.fails, self.batches = fails, []

    async def collection_exists(self, _name: str) -> bool:
        return True

    async def create_payload_index(self, *_: Any, **__: Any) -> None:
        return None

    async def upsert(self, _collection: str, points: list[Any]) -> None:
        if self.fails:
            self.fails -= 1
            raise httpx.WriteTimeout("write timed out")
        self.batches.append(len(points))


def jobs(n: int) -> list[dict[str, Any]]:
    """n job rows."""
    return [{"id": i, "title": f"AI Engineer {i}", "company": "Acme", "company_norm": "acme",
             "title_norm": "ai engineer", "jd_text": "Python LLM RAG"} for i in range(1, n + 1)]  # fmt: skip


async def test_a_network_hiccup_is_retried_and_uploads_are_batched(monkeypatch: pytest.MonkeyPatch) -> None:
    """One timeout, then success: every point is stored in batches of 64."""
    monkeypatch.setattr(vectors_mod.asyncio, "sleep", lambda _s: _noop())
    client = FlakyClient(fails=1)
    store = VectorStore(client, 8)  # type: ignore[arg-type]
    await store.upsert("jobs", [(i, [0.1] * 8, {}) for i in range(103)])
    assert client.batches == [64, 39]


async def test_a_qdrant_outage_never_stops_scoring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every try times out (run 18): stage A still returns a score for all 103 jobs."""
    monkeypatch.setattr(vectors_mod.asyncio, "sleep", lambda _s: _noop())
    store = VectorStore(FlakyClient(fails=99), 384)  # type: ignore[arg-type]
    embedder = HashEmbedder()
    (profile,) = await embedder.embed(["Python LLM RAG LangGraph"])
    scores = await embed_score.stage_a(jobs(103), profile, embedder, store)
    assert len(scores) == 103 and all(-1 <= s <= 1 for s in scores.values())


async def _noop() -> None:
    return None
