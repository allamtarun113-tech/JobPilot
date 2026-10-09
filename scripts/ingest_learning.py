"""Rebuild the learning_resources collection in Qdrant from Data/learning.

Local embeddings only, no model call."""

import asyncio

from jobpilot.integrations.vectors import VectorStore
from jobpilot.knowledge.learning import ingest
from jobpilot.llm.embeddings import default_embedder


async def main() -> None:
    """Rebuild the learning_resources collection."""
    vectors = VectorStore.from_settings()
    try:
        print(f"ingested {await ingest(vectors, default_embedder())} learning resources")
    finally:
        await vectors.close()


if __name__ == "__main__":
    asyncio.run(main())
