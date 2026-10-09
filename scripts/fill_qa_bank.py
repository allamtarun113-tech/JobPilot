"""Copy the questions of every saved prep set into the Q&A bank.

Safe to run again: the same question from the same source is the same point."""

import asyncio

from sqlalchemy import select

from jobpilot.db.session import dispose_engine, read
from jobpilot.db.tables import jobs, prep_sets
from jobpilot.integrations.vectors import VectorStore
from jobpilot.llm.embeddings import default_embedder
from jobpilot.models.prep import Question
from jobpilot.prep import qa_bank


async def main() -> None:
    """Save the questions of every ready prep set into the Q&A bank."""
    async with read() as conn:
        rows = (
            await conn.execute(
                select(prep_sets.c.questions, jobs.c.id, jobs.c.company, jobs.c.title)
                .join(jobs, jobs.c.id == prep_sets.c.job_id)
                .where(prep_sets.c.status == "ready")
            )
        ).all()
    vectors, embedder = VectorStore.from_settings(), default_embedder()
    saved = 0
    try:
        for row in rows:
            questions = [Question.model_validate(q) for q in row.questions or []]
            job = {"id": row.id, "company": row.company, "title": row.title}
            saved += await qa_bank.add(vectors, embedder, qa_bank.from_prep_set(job, questions))
    finally:
        await vectors.close()
        await dispose_engine()
    print(f"{saved} questions from {len(rows)} prep sets saved to the Q&A bank")


if __name__ == "__main__":
    asyncio.run(main())
