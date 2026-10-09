"""Rebuild the heading-aware Prepare corpus in Qdrant from Data/books/books.json."""

import asyncio

from jobpilot.integrations.vectors import VectorStore
from jobpilot.llm.embeddings import default_embedder
from jobpilot.prep.ingest import ingest_all


async def main() -> None:
    """Rebuild the prep_chunks collection from the prep books."""
    vectors = VectorStore.from_settings()
    try:
        result = await ingest_all(vectors, default_embedder())
        print(f"ingested {result['books']} books as {result['chunks']} heading sections")
    finally:
        await vectors.close()


if __name__ == "__main__":
    asyncio.run(main())
