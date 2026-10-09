"""Replay a run's scoring decisions with the current code - no model calls, no side effects.

For every job a run touched, the cached requirements (same JD hash, prompt version and model) are re-scored
against today's confirmed profile, and the old and new scores are printed side by side.
Use it after changing the rubric, the taxonomy or the profile ("same JD + same profile = same score").

Usage: uv run python scripts/replay_run.py <run_id>
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.queries import requirements
from jobpilot.db.session import dispose_engine, transaction
from jobpilot.db.tables import job_sources, scores
from jobpilot.integrations.vectors import VectorStore
from jobpilot.llm.embeddings import default_embedder
from jobpilot.matching import compute, profile_view
from jobpilot.models.requirements import Requirements


async def main(run_id: int) -> None:
    """Re-score every job a run touched and print the old and new scores."""
    async with transaction() as conn:
        job_ids = sorted(
            {
                r[0]
                for r in (
                    await conn.execute(select(job_sources.c.job_id).where(job_sources.c.run_id == run_id))
                ).all()
            }
        )
        view = await profile_view.load(conn)
    vectors, embedder = VectorStore.from_settings(), default_embedder()
    print(f"run {run_id}: {len(job_ids)} jobs")
    for job_id in job_ids:
        async with transaction() as conn:
            job = await jobs_repo.get(conn, job_id)
            old = (
                (
                    await conn.execute(
                        select(scores).where(scores.c.job_id == job_id).order_by(scores.c.id.desc()).limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )
            req = await requirements.get(conn, old["requirements_id"]) if old else None
        if job is None or old is None or req is None:
            continue
        new = await compute.compute(
            Requirements.model_validate(req["data"]),
            view,
            embedder=embedder,
            vectors=vectors,
            jd_is_snippet=bool(job["jd_is_snippet"]),
        )
        print(f"  job {job_id} {job['company']} - {job['title']}: {float(old['score']):g} -> {new.score:g}")
    await vectors.close()
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1])))
