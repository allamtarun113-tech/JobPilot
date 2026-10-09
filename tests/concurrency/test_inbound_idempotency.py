"""A repeated Telegram update is stored once and produces one piece of work and one reply."""

import asyncio

import pytest
from sqlalchemy import func, select

from jobpilot.bot import router
from jobpilot.db.session import transaction
from jobpilot.db.tables import events, outbox, work_items
from tests.conftest import telegram_update

pytestmark = pytest.mark.usefixtures("db")


async def count(table) -> int:  # type: ignore[no-untyped-def]
    """Return the row count of a table."""
    async with transaction() as conn:
        return (await conn.execute(select(func.count()).select_from(table))).scalar_one()


async def test_repeated_update_is_a_noop() -> None:
    """Five copies of one update store one event, one work item and one reply."""
    update = telegram_update(77, "/status")
    ids = await asyncio.gather(*(router.ingest_update(update) for _ in range(5)))
    assert len([i for i in ids if i is not None]) == 1
    assert await count(events) == 1
    assert await count(work_items) == 1
    event_id = next(i for i in ids if i is not None)
    await router.handle_event(event_id)
    await router.handle_event(event_id)  # the work item being retried
    assert await count(outbox) == 1


async def test_other_chats_are_ignored() -> None:
    """An update from another chat is not stored."""
    assert await router.ingest_update(telegram_update(78, "/start", chat_id=1)) is None
    assert await count(events) == 0
