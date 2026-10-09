"""HR email delivery: tokens minted at delivery, shadow mode, and the 24-hour rule."""

import pytest
from sqlalchemy import select

from jobpilot.core.clock import FrozenClock
from jobpilot.core.config import get_config
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import emails as emails_repo
from jobpilot.db.queries import resume_versions as versions_repo
from jobpilot.db.session import transaction
from jobpilot.db.tables import outbox as outbox_table
from jobpilot.db.tables import sent_emails
from jobpilot.dispatcher.delivery import Dispatcher
from jobpilot.hr_email import drafts, send
from jobpilot.models.card import CardStatus
from jobpilot.models.email import EmailDraft
from tests.conftest import FakeSender
from tests.integration.test_card_transitions import setup_card

pytestmark = pytest.mark.usefixtures("db")


async def _draft_ready() -> tuple[int, int]:
    """Create an applied card with an email draft already created in Gmail.

    Returns:
        (card id, email id).
    """
    card_id, _ = await setup_card()
    async with transaction() as conn:
        card = await cards_repo.get(conn, card_id)
        assert card is not None
        resume = await versions_repo.latest(conn, card["job_id"])
        assert resume is not None
        await cards_repo.update(
            conn,
            card_id,
            card["version"],
            status=CardStatus.APPLIED.value,
            approved_resume_version_id=resume["id"],
        )
    email_id = await drafts.create(
        card["job_id"],
        resume["id"],
        "careers@example.com",
        "job description",
        EmailDraft(subject="Application", body="I built Python services.", citations=[1]),
    )
    gmail = FakeSender()
    dispatcher = Dispatcher(
        {"gmail": gmail, "telegram": FakeSender()}, get_config().dispatcher, shadow_mode=False
    )
    assert await dispatcher.deliver_ready() == 3
    return card_id, email_id


async def test_dispatcher_mints_token_only_at_delivery_and_records_send_once() -> None:
    """The queued row holds no token; delivery adds one and records one send."""
    card_id, email_id = await _draft_ready()
    await send.approve_and_queue(card_id, email_id)
    async with transaction() as conn:
        row = (
            (
                await conn.execute(
                    select(outbox_table)
                    .where(outbox_table.c.kind == "gmail")
                    .order_by(outbox_table.c.id.desc())
                )
            )
            .mappings()
            .first()
        )
        assert row is not None and "approval_token" not in row["payload"]

    gmail = FakeSender()
    dispatcher = Dispatcher({"gmail": gmail}, get_config().dispatcher, shadow_mode=False)
    assert await dispatcher.deliver_ready() == 1
    assert "approval_token" in gmail.calls[0]
    async with transaction() as conn:
        card = await cards_repo.get(conn, card_id)
        email = await emails_repo.get(conn, email_id)
        sent = (await conn.execute(select(sent_emails))).mappings().all()
    assert card is not None and card["status"] == CardStatus.EMAILED.value
    assert email is not None and email["status"] == "sent"
    assert len(sent) == 1
    assert await dispatcher.deliver_ready() == 0


async def test_shadow_mode_never_calls_gmail_send() -> None:
    """In shadow mode the send is logged, Gmail isn't called and the card stays applied."""
    card_id, email_id = await _draft_ready()
    await send.approve_and_queue(card_id, email_id)
    gmail = FakeSender()
    dispatcher = Dispatcher({"gmail": gmail}, get_config().dispatcher, shadow_mode=True)
    assert await dispatcher.deliver_ready() == 1
    assert gmail.calls == []
    async with transaction() as conn:
        card = await cards_repo.get(conn, card_id)
        email = await emails_repo.get(conn, email_id)
        sent = (await conn.execute(select(sent_emails))).all()
    assert card is not None and card["status"] == CardStatus.APPLIED.value
    assert email is not None and email["status"] == "shadowed"
    assert sent == []


async def test_approval_older_than_24_hours_requires_reconfirmation(
    frozen_clock: FrozenClock,
) -> None:
    """A send approved over 24 hours ago isn't sent; the card asks again."""
    card_id, email_id = await _draft_ready()
    await send.approve_and_queue(card_id, email_id)
    frozen_clock.advance(hours=24)
    gmail = FakeSender()
    dispatcher = Dispatcher({"gmail": gmail}, get_config().dispatcher, shadow_mode=False)
    assert await dispatcher.deliver_ready() == 0
    assert gmail.calls == []
    async with transaction() as conn:
        card = await cards_repo.get(conn, card_id)
        email = await emails_repo.get(conn, email_id)
    assert card is not None and card["status"] == CardStatus.NEEDS_RECONFIRM.value
    assert email is not None and email["status"] == "needs_reconfirm"
