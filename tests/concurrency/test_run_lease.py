"""Double /start, stale-lease takeover, and a lost lease."""

import asyncio

import pytest
from sqlalchemy import func, select

from jobpilot.core.clock import FrozenClock
from jobpilot.db.session import transaction
from jobpilot.db.tables import runs
from jobpilot.run import lease

pytestmark = pytest.mark.usefixtures("db")


async def count_runs() -> int:
    """Return how many runs exist."""
    async with transaction() as conn:
        return (await conn.execute(select(func.count()).select_from(runs))).scalar_one()


async def test_concurrent_acquire_yields_one_run() -> None:
    """Five simultaneous /starts create exactly one run."""
    results = await asyncio.gather(*(lease.acquire(f"w{i}", 5, f"req{i}") for i in range(5)))
    acquired = [r for r in results if isinstance(r, lease.Acquired)]
    held = [r for r in results if isinstance(r, lease.Held)]
    assert len(acquired) == 1 and len(held) == 4
    assert await count_runs() == 1


async def test_stale_lease_is_taken_over_and_old_holder_loses_it(frozen_clock: FrozenClock) -> None:
    """A crashed holder's stale lease is taken over; its heartbeat then fails."""
    first = await lease.acquire("w1", 5, "r1")
    assert isinstance(first, lease.Acquired) and not first.resumed
    frozen_clock.advance(seconds=30)
    assert isinstance(await lease.acquire("w2", 5, "r2"), lease.Held)  # still fresh
    frozen_clock.advance(seconds=200)  # w1 crashed: no heartbeat
    second = await lease.acquire("w2", 5, "r3")
    assert isinstance(second, lease.Acquired) and second.resumed and second.run_id == first.run_id
    assert await lease.heartbeat("w1") == 0
    assert await lease.heartbeat("w2") == 1
    assert await count_runs() == 1


async def test_heartbeat_keeps_lease_fresh(frozen_clock: FrozenClock) -> None:
    """Regular heartbeats keep the lease from being taken."""
    await lease.acquire("w1", 5, "r1")
    for _ in range(10):
        frozen_clock.advance(seconds=60)
        await lease.heartbeat("w1")
    assert isinstance(await lease.acquire("w2", 5, "r2"), lease.Held)
