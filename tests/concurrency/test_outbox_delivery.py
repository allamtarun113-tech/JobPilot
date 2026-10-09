"""Outbox: atomic with its state change, delivered once after a crash, ordered per partition."""

import pytest
from sqlalchemy import func, select

from jobpilot.core.clock import FrozenClock
from jobpilot.core.config import get_config
from jobpilot.db.queries import flags
from jobpilot.db.queries import runs as runs_repo
from jobpilot.db.session import transaction
from jobpilot.db.tables import outbox as outbox_t
from jobpilot.db.tables import runs
from jobpilot.dispatcher.delivery import Dispatcher
from jobpilot.queue import outbox
from tests.conftest import FakeSender

pytestmark = pytest.mark.usefixtures("db")


def dispatcher(sender: FakeSender, shadow: bool = True) -> Dispatcher:
    """Build a dispatcher whose senders are the given fake."""
    return Dispatcher({"telegram": sender, "gmail": sender}, get_config().dispatcher, shadow_mode=shadow)


async def statuses() -> list[tuple[int, str]]:
    """Return (seq, status) of every outbox row, oldest first."""
    async with transaction() as conn:
        rows = await conn.execute(select(outbox_t.c.seq, outbox_t.c.status).order_by(outbox_t.c.id))
        return [tuple(r) for r in rows.all()]  # type: ignore[misc]


async def test_state_change_and_message_commit_together_or_not_at_all() -> None:
    """A rolled-back transaction leaves neither the run nor its message."""
    with pytest.raises(RuntimeError):
        async with transaction() as conn:
            await runs_repo.create(conn, holder="w", card_cap=1)
            await outbox.telegram_message(conn, "started", idempotency_key="k1")
            raise RuntimeError("crash before commit")
    async with transaction() as conn:
        assert (await conn.execute(select(func.count()).select_from(runs))).scalar_one() == 0
        assert (await conn.execute(select(func.count()).select_from(outbox_t))).scalar_one() == 0


async def test_crash_before_dispatch_delivers_exactly_once_later() -> None:
    """A message committed before a crash is delivered once by a later dispatcher."""
    async with transaction() as conn:
        await runs_repo.create(conn, holder="w", card_cap=1)
        await outbox.telegram_message(conn, "started", idempotency_key="k1")
    # The process "crashed" here: nothing was dispatched. A fresh dispatcher starts later.
    sender = FakeSender()
    assert await dispatcher(sender).deliver_ready() == 1
    assert await dispatcher(sender).deliver_ready() == 0
    assert [c["text"] for c in sender.calls] == ["started"]
    assert await statuses() == [(1, "delivered")]


async def test_same_idempotency_key_is_one_row() -> None:
    """Enqueueing the same key twice gives one row."""
    async with transaction() as conn:
        a = await outbox.telegram_message(conn, "x", idempotency_key="same")
        b = await outbox.telegram_message(conn, "x", idempotency_key="same")
    assert a == b
    assert len(await statuses()) == 1


async def test_edit_queued_before_its_send_waits_for_it(frozen_clock: FrozenClock) -> None:
    """An edit never overtakes the failing send it targets."""
    part = outbox.card_partition(1)
    async with transaction() as conn:
        send_id = await outbox.telegram_message(conn, "card v1", idempotency_key="s", partition_key=part)
        await outbox.telegram_edit(
            conn, "card v2", target_outbox_id=send_id, idempotency_key="e", partition_key=part
        )
    sender = FakeSender(failures=[True])  # the send fails once
    d = dispatcher(sender)
    assert await d.deliver_ready() == 0
    assert [c["op"] for c in sender.calls] == ["send_message"]  # the edit did not overtake
    frozen_clock.advance(seconds=10)
    assert await d.deliver_ready() == 2
    ops = [(c["op"], c.get("message_id")) for c in sender.calls]
    assert ops == [("send_message", None), ("send_message", None), ("edit_message", 501)]


async def test_dead_head_pauses_only_its_partition() -> None:
    """A dead row blocks its own partition only, and raises one alert."""
    async with transaction() as conn:
        await outbox.telegram_message(conn, "a1", idempotency_key="a1", partition_key="card:1")
        await outbox.telegram_message(conn, "a2", idempotency_key="a2", partition_key="card:1")
        await outbox.telegram_message(conn, "b1", idempotency_key="b1", partition_key="card:2")
    sender = FakeSender(failures=[True], retryable=False)
    await dispatcher(sender).deliver_ready()
    texts = [c["text"] for c in sender.calls]
    assert "a2" not in texts and "b1" in texts
    async with transaction() as conn:
        rows = dict((await conn.execute(select(outbox_t.c.idempotency_key, outbox_t.c.status))).all())
    alerts = {k: v for k, v in rows.items() if k.startswith("out:alert:")}
    assert {k: v for k, v in rows.items() if k not in alerts} == {
        "a1": "dead",
        "a2": "pending",
        "b1": "delivered",
    }
    assert len(alerts) == 1  # a dead row raises an alert, in its own partition (so it still gets delivered)


async def test_shadow_mode_refuses_incomplete_unapproved_send() -> None:
    """A send without an approval is skipped, never delivered."""
    async with transaction() as conn:
        await outbox.enqueue(
            conn,
            partition_key="card:1",
            kind="gmail",
            payload={"op": "send_draft", "draft_id": "d1"},
            idempotency_key="g1",
        )
    sender = FakeSender()
    assert await dispatcher(sender, shadow=True).deliver_ready() == 0
    assert sender.calls == []
    async with transaction() as conn:
        ext = (await conn.execute(select(outbox_t.c.external_id))).scalar_one()
    assert ext is None
    async with transaction() as conn:
        assert await conn.scalar(select(outbox_t.c.status)) == "skipped"


async def test_paused_dispatcher_delivers_nothing() -> None:
    """While paused nothing is delivered; after resuming it is."""
    async with transaction() as conn:
        await outbox.telegram_message(conn, "x", idempotency_key="x")
        await flags.put(conn, flags.DISPATCHER_PAUSED, {"on": True})
    sender = FakeSender()
    assert await dispatcher(sender).deliver_ready() == 0
    async with transaction() as conn:
        await flags.put(conn, flags.DISPATCHER_PAUSED, {"on": False})
    assert await dispatcher(sender).deliver_ready() == 1


async def test_outbox_refuses_tokens_in_payload() -> None:
    """A payload with a token field is refused."""
    with pytest.raises(ValueError):
        async with transaction() as conn:
            await outbox.enqueue(
                conn,
                partition_key="card:1",
                kind="gmail",
                payload={"op": "send_draft", "token": "secret"},
                idempotency_key="t",
            )
