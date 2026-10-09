"""Re-score jobs with the current rubric (decision 29), e.g. after the scoring changed.

    make rescore                 # every job scored but not carded (queued or below your bar)
    make rescore ARGS="--limit 20"

Each job goes through the normal pipeline again: requirements are re-read with the current prompt (one
gpt-4o-mini call per JD, cached afterwards), the score and the skills to learn are recomputed by code, and
the job is queued or marked below your bar. Free lookups only: no web searches. Carded jobs are left alone.
Run it while no /start run is active.
"""

import argparse
import asyncio

from sqlalchemy import select

from jobpilot.db.session import dispose_engine, read
from jobpilot.db.tables import jobs
from jobpilot.integrations.vectors import VectorStore
from jobpilot.llm.client import LLMClient
from jobpilot.llm.embeddings import default_embedder
from jobpilot.models.job import PipelineStatus
from jobpilot.run import context, pipeline
from jobpilot.run.deps import RunDeps, set_deps
from jobpilot.sources.enrich_lookup import EnrichLookup


async def main() -> None:
    """Run queued and below-bar jobs through the pipeline again."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()
    async with read() as conn:
        ids = [
            r.id
            for r in (
                await conn.execute(
                    select(jobs.c.id)
                    .where(
                        jobs.c.pipeline_status.in_(
                            [PipelineStatus.QUEUED.value, PipelineStatus.BELOW_BAR.value]
                        )
                    )
                    .order_by(jobs.c.id.desc())
                    .limit(args.limit)
                )
            ).all()
        ]
    vectors = VectorStore.from_settings()
    set_deps(
        RunDeps(
            embedder=default_embedder(),
            vectors=vectors,
            llm=LLMClient(),
            jobs_api=None,
            enrich=EnrichLookup(None),
        )
    )
    outcomes: dict[str, int] = {}
    try:
        ctx = await context.build()
        await ctx.profile_vector()  # re-index the profile (skills included) before scoring
        job_ctx = ctx.job_context("rescore-script", None)
        for job_id in ids:
            try:
                status = await pipeline.process(job_id, job_ctx)
            except Exception as exc:
                status = f"error ({type(exc).__name__})"
            outcomes[status] = outcomes.get(status, 0) + 1
            print(f"  job {job_id}: {status}", flush=True)
    finally:
        await vectors.close()
        await dispose_engine()
    print(f"re-scored {len(ids)} jobs: {outcomes}")


if __name__ == "__main__":
    asyncio.run(main())
