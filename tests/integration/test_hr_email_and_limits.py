"""HR email from Telegram (tap, reply with an address, writer retry) and the daily limits."""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select, update

from jobpilot.bot import callbacks, router
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import resume_versions as versions_repo
from jobpilot.db.session import transaction
from jobpilot.db.tables import emails, outbox, work_items
from jobpilot.models.card import CardStatus
from jobpilot.models.email import EmailPoints, FitPoint
from jobpilot.models.work import EMAIL_DRAFT
from tests.conftest import OWNER_CHAT
from tests.integration.test_card_transitions import setup_card

pytestmark = pytest.mark.usefixtures("db")


async def _applied_card() -> tuple[int, int, str]:
    """Create an applied card whose message was delivered as message 900.

    Returns:
        (card id, job id, button token).
    """
    card_id, token = await setup_card()
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
        # The card message was delivered as Telegram message 900.
        await conn.execute(
            update(outbox).where(outbox.c.idempotency_key == "test-card").values(external_id="900")
        )
    return card_id, int(card["job_id"]), token


async def _drafts() -> list[dict[str, Any]]:
    """Return every queued email-draft work item."""
    async with transaction() as conn:
        rows = await conn.execute(select(work_items).where(work_items.c.kind == EMAIL_DRAFT))
        return [dict(r) for r in rows.mappings()]


async def test_email_tap_answers_at_once_and_queues_the_draft() -> None:
    """Tapping Email HR queues one draft for the job."""
    _, job_id, token = await _applied_card()
    await callbacks.handle(77, {"id": "q", "data": f"em:{token}"})
    queued = await _drafts()
    assert [(d["payload"]["job_id"], d["payload"].get("address")) for d in queued] == [(job_id, None)]


async def test_a_reply_with_an_address_drafts_the_email_to_it() -> None:
    """Replying to a card with an address queues a draft to that address."""
    _, job_id, _ = await _applied_card()
    update_ = {
        "update_id": 5001,
        "message": {
            "message_id": 5001,
            "chat": {"id": OWNER_CHAT},
            "text": "hr@acme-careers.com",
            "reply_to_message": {"message_id": 900},
        },
    }
    event_id = await router.ingest_update(update_)
    assert event_id is not None
    await router.handle_event(event_id)
    queued = await _drafts()
    assert [(d["payload"]["job_id"], d["payload"]["address"]) for d in queued] == [
        (job_id, "hr@acme-careers.com")
    ]


class EmailLLM:
    """A fake writer: an unbacked sentence first, then the first fact as it is; records prompts."""

    def __init__(self) -> None:
        """Start with no prompts."""
        self.prompts: list[Any] = []

    async def structured(self, task: str, prompt: Any, output: type, **_: Any) -> Any:
        """Record the prompt and return the next fit sentence about the first listed fact."""
        self.prompts.append(prompt)
        fact_id, text = re.search(r'"(\d+)": "([^"]+)"', prompt.user).groups()  # type: ignore[union-attr]
        sentence = "I am a Rust expert with Python." if len(self.prompts) == 1 else text
        points = EmailPoints(points=[FitPoint(fact_id=int(fact_id), sentence=sentence)])
        return SimpleNamespace(output=points, prompt_id="hr_email/email_writer_v4", model="m")


async def test_the_writer_retries_once_with_what_the_checks_refused() -> None:
    """A refused sentence is retried once, told why; the faithful second one is stored with its citation."""
    from jobpilot.hr_email.service import compose

    _, job_id, _ = await _applied_card()
    llm = EmailLLM()
    email_id = await compose(job_id, llm, address_override="careers@acme.com")  # type: ignore[arg-type]
    assert "Rust" in llm.prompts[1].user  # the retry was told about the unbacked skill
    async with transaction() as conn:
        row = (await conn.execute(select(emails).where(emails.c.id == email_id))).mappings().one()
    assert row["to_address"] == "careers@acme.com" and "Rust" not in row["body"]
    assert row["citations"] and row["body"].rstrip().splitlines()[-3] == "Best regards,"


async def test_card_limit_follows_your_preference_up_to_the_ceiling() -> None:
    """The card limit follows the user's preference."""
    from jobpilot.profile import preferences
    from jobpilot.safety import caps

    async with transaction() as conn:
        prefs, version = await preferences.load(conn)
        await preferences.save(conn, prefs.model_copy(update={"cards_per_day": 20}), version)
    async with transaction() as conn:
        assert await caps.card_limit(conn) == 20


async def test_checkpointer_pool_survives_a_closed_connection() -> None:
    """The checkpointer pool replaces a connection the pooler closed."""
    from contextlib import AsyncExitStack

    from jobpilot.worker.main import open_checkpointer

    config = {"configurable": {"thread_id": "pool-test", "checkpoint_ns": ""}}
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(stack)
        assert await saver.aget_tuple(config) is None  # type: ignore[arg-type]
        pool = saver.conn
        async with pool.connection() as conn:  # type: ignore[union-attr]
            await conn.close()  # what the cloud pooler did after a few idle hours
        assert await saver.aget_tuple(config) is None  # type: ignore[arg-type]  # a fresh connection is used


async def test_no_daily_token_limit_unless_a_number_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no token limit in config the budget is unlimited."""
    from jobpilot.core import config
    from jobpilot.safety import caps
    from tests.conftest import _override_defaults

    async with transaction() as conn:
        assert await caps.token_budget_left(conn) == caps.UNLIMITED  # config/defaults.yaml: null
    _override_defaults(
        monkeypatch, budgets={**config.load_yaml("defaults.yaml")["budgets"], "tokens_per_day": 1000}
    )
    try:
        async with transaction() as conn:
            assert await caps.token_budget_left(conn) == 1000
    finally:
        monkeypatch.undo()
        config.get_config.cache_clear()
