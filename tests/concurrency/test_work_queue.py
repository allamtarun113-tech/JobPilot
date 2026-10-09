"""SKIP LOCKED: concurrent claimers never get the same item."""

import asyncio

import pytest

from jobpilot.db.session import transaction
from jobpilot.queue import jobs_queue

pytestmark = pytest.mark.usefixtures("db")


async def test_concurrent_claims_are_disjoint() -> None:
    """Four claimers split 40 items with no item claimed twice."""
    async with transaction() as conn:
        for i in range(40):
            await jobs_queue.enqueue(conn, kind="x", payload={"i": i}, idempotency_key=f"k{i}")

    async def claimer(name: str) -> list[int]:
        """Claim batches until the queue is empty; return the claimed ids."""
        got: list[int] = []
        while True:
            async with transaction() as conn:
                items = await jobs_queue.claim(conn, holder=name, limit=3, ttl_seconds=60)
            if not items:
                return got
            got += [i["id"] for i in items]

    results = await asyncio.gather(*(claimer(f"w{i}") for i in range(4)))
    all_ids = [i for r in results for i in r]
    assert len(all_ids) == 40 and len(set(all_ids)) == 40
