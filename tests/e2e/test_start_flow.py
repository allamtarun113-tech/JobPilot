"""A /start yields one run, one summary from the outbox and one trace.

A repeated update or a second /start does nothing extra.
"""

import asyncio
from typing import Any

import pytest
from sqlalchemy import func, select

from jobpilot.bot import router
from jobpilot.core.config import get_config
from jobpilot.db.session import transaction
from jobpilot.db.tables import runs
from jobpilot.dispatcher.delivery import Dispatcher
from jobpilot.run import lease
from jobpilot.worker.main import make_handlers
from jobpilot.worker.queue_loop import QueueLoop
from tests.conftest import FakeSender, confirmed_profile, telegram_update

pytestmark = pytest.mark.usefixtures("db")
HOLDER = "worker-test"


@pytest.fixture(autouse=True)
async def _ready(db: None, p2_deps: Any) -> None:
    """A run needs a confirmed profile (preflight) and run dependencies (fakes here)."""
    await confirmed_profile()


async def drain_work(checkpointer: Any) -> None:
    """Run the worker's queue loop until no work is left."""
    loop = QueueLoop(HOLDER, make_handlers(HOLDER, checkpointer))
    while await loop.run_once() or loop.tasks:
        await loop.drain()


async def deliver_all() -> list[str]:
    """Deliver every pending Telegram message and return their texts."""
    sender = FakeSender()
    await Dispatcher({"telegram": sender}, get_config().dispatcher, shadow_mode=True).deliver_ready()
    return [c["text"] for c in sender.calls if "text" in c]


async def run_rows() -> list[Any]:
    """Return every run row, oldest first."""
    async with transaction() as conn:
        return list((await conn.execute(select(runs).order_by(runs.c.id))).mappings())


async def test_start_produces_one_run_one_summary_one_trace(checkpointer: Any, spans: Any) -> None:
    """A repeated /start gives one run, two messages and one trace with every step."""
    update = telegram_update(1001, "/start 3")
    await router.ingest_update(update)
    await router.ingest_update(update)  # Telegram repeats the update
    await drain_work(checkpointer)

    rows = await run_rows()
    assert len(rows) == 1
    run = rows[0]
    assert run["status"] == "finished" and run["stop_reason"] == "queue_empty"
    assert run["lease_holder"] is None and run["card_cap"] == 3

    texts = await deliver_all()
    assert len(texts) == 2
    assert texts[0].startswith(f"Run #{run['id']} started (up to 3 cards)")
    assert texts[1].startswith(f"Run #{run['id']} finished")

    run_spans = [s for s in spans.get_finished_spans() if s.name == "run"]
    assert len(run_spans) == 1
    trace_id = format(run_spans[0].context.trace_id, "032x")
    assert run["trace_id"] == trace_id
    node_spans = {
        s.name for s in spans.get_finished_spans() if format(s.context.trace_id, "032x") == trace_id
    }
    assert {"run.preflight", "run.fetch", "run.process_job", "run.drain", "run.summary"} <= node_spans


async def test_second_start_while_running_is_refused(checkpointer: Any) -> None:
    """A /start while another run holds the lease starts nothing new."""
    held = await lease.acquire("other-worker", 5, "manual")  # a live run holds the lease
    assert isinstance(held, lease.Acquired)
    await router.ingest_update(telegram_update(2001, "/start"))
    await drain_work(checkpointer)
    assert len(await run_rows()) == 1
    texts = await deliver_all()
    assert any("already in progress" in t for t in texts)


async def test_stop_and_status_commands(checkpointer: Any) -> None:
    """/stop, /status and unknown commands each get the right reply."""
    await router.ingest_update(telegram_update(3001, "/stop"))
    await router.ingest_update(telegram_update(3002, "/status"))
    await router.ingest_update(telegram_update(3003, "/whatever"))
    await drain_work(checkpointer)
    texts = await deliver_all()
    assert any("No run is in progress" in t for t in texts)
    assert any("Work items pending" in t for t in texts)
    assert any("JobPilot commands" in t for t in texts)


async def test_stop_request_makes_a_live_run_drain(checkpointer: Any) -> None:
    """/stop on a live run sets its stop flag and confirms."""
    got = await lease.acquire(HOLDER, 5, "manual")
    assert isinstance(got, lease.Acquired)
    await router.ingest_update(telegram_update(4001, "/stop"))
    await drain_work(checkpointer)
    async with transaction() as conn:
        stop_requested = (await conn.execute(select(runs.c.stop_requested))).scalar_one()
    assert stop_requested is True
    texts = await deliver_all()
    assert any("Stopping run" in t for t in texts)


async def test_crashed_run_is_resumed_by_next_start(checkpointer: Any, frozen_clock: Any) -> None:
    """A run with a stale lease is resumed and finished by the next /start."""
    got = await lease.acquire("crashed-worker", 5, "manual")
    assert isinstance(got, lease.Acquired)
    frozen_clock.advance(seconds=600)  # no heartbeats: the lease is stale
    await router.ingest_update(telegram_update(5001, "/start"))
    await drain_work(checkpointer)
    rows = await run_rows()
    assert len(rows) == 1 and rows[0]["status"] == "finished"
    texts = await deliver_all()
    assert any("resuming" in t for t in texts) and any("finished" in t for t in texts)


async def test_runs_can_follow_each_other(checkpointer: Any) -> None:
    """Two /starts one after the other give two finished runs."""
    for uid in (6001, 6002):
        await router.ingest_update(telegram_update(uid, "/start"))
        await drain_work(checkpointer)
    rows = await run_rows()
    assert [r["status"] for r in rows] == ["finished", "finished"]
    await asyncio.sleep(0)
    async with transaction() as conn:
        assert (await conn.execute(select(func.count()).select_from(runs))).scalar_one() == 2


async def test_prefs_command_shows_and_changes(checkpointer: Any) -> None:
    """/prefs shows, changes and refuses invalid values."""
    await router.ingest_update(telegram_update(7001, "/prefs"))
    await router.ingest_update(telegram_update(7002, "/prefs cards 8"))
    await router.ingest_update(telegram_update(7003, "/prefs threshold 500"))
    await drain_work(checkpointer)
    texts = await deliver_all()
    assert any(t.startswith("Your preferences:") for t in texts)
    assert "Updated cards per day: 50 -> 8" in texts
    assert any(t.startswith("Not changed: threshold must be 0-100") for t in texts)
