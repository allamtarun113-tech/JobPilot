"""Repository rules: version-checked updates and row leases."""

import pytest
from sqlalchemy import select

from jobpilot.core.clock import FrozenClock
from jobpilot.core.errors import VersionConflict
from jobpilot.db import leases
from jobpilot.db.queries import source_cursors as cursors_repo
from jobpilot.db.queries import work_items as work_repo
from jobpilot.db.session import transaction
from jobpilot.db.tables import source_cursors, work_items

pytestmark = pytest.mark.usefixtures("db")


async def test_versioned_update_refuses_stale_version() -> None:
    """A save with an old version is refused and the newer cursor stays."""
    async with transaction() as conn:
        await cursors_repo.save(conn, "alerts", {"history_id": 1}, expected_version=None)
    async with transaction() as conn:
        assert await cursors_repo.save(conn, "alerts", {"history_id": 2}, expected_version=1) == 2
    with pytest.raises(VersionConflict):
        async with transaction() as conn:
            await cursors_repo.save(conn, "alerts", {"history_id": 3}, expected_version=1)
    async with transaction() as conn:
        row = (await conn.execute(select(source_cursors.c.cursor))).scalar_one()
    assert row == {"history_id": 2}


async def test_row_lease_excludes_others_until_expiry(frozen_clock: FrozenClock) -> None:
    """A row lease excludes others until it expires; only the holder releases it."""
    async with transaction() as conn:
        item_id = await work_repo.enqueue(conn, kind="x", payload={}, idempotency_key=None)
    assert item_id is not None
    async with transaction() as conn:
        assert await leases.take(conn, work_items, item_id, "a", ttl_seconds=60)
        assert not await leases.take(conn, work_items, item_id, "b", ttl_seconds=60)
    frozen_clock.advance(seconds=61)
    async with transaction() as conn:
        assert await leases.take(conn, work_items, item_id, "b", ttl_seconds=60)
        assert not await leases.release(conn, work_items, item_id, "a")
        assert await leases.release(conn, work_items, item_id, "b")
