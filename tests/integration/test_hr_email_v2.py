"""HR email v2 (decision 33): quiet automatic drafts, shown on tap.

An automatic, quiet draft for every card with an agent-found address
that code verifies; Email HR shows it at once with the approved
resume; nothing is sent without your tap.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.session import transaction
from jobpilot.db.tables import emails, outbox
from jobpilot.hr_email import send
from jobpilot.hr_email.service import compose
from jobpilot.models.email import EmailPoints
from tests.integration.test_card_transitions import setup_card
from tests.integration.test_hr_email_and_limits import _applied_card

pytestmark = pytest.mark.usefixtures("db")


class Writer:
    """A fake email writer that counts calls and records prompts."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.calls = 0
        self.prompts: list[Any] = []

    async def structured(self, task: str, prompt: Any, output: type, **_: Any) -> Any:
        """Record the prompt and return no fit sentences (code writes the whole email around the role)."""
        self.calls += 1
        self.prompts.append(prompt)
        return SimpleNamespace(output=EmailPoints(points=[]), prompt_id="hr_email/email_writer_v4", model="m")


def agent(found: list[dict[str, str]]):  # type: ignore[no-untyped-def]
    """Return a fake hiring-email search that returns ``found``."""
    calls: list[str] = []

    async def contacts(company: str, job_id: int) -> list[dict[str, str]]:
        """Record the company and return the fixed addresses."""
        calls.append(company)
        return found

    contacts.calls = calls  # type: ignore[attr-defined]
    return contacts


async def rows(job_id: int) -> list[dict[str, Any]]:
    """Return a job's email rows, oldest version first."""
    async with transaction() as conn:
        result = await conn.execute(
            select(emails).where(emails.c.job_id == job_id).order_by(emails.c.version)
        )
        return [dict(r) for r in result.mappings()]


async def gmail_rows() -> int:
    """Return how many Gmail outbox rows exist."""
    async with transaction() as conn:
        return len((await conn.execute(select(outbox.c.id).where(outbox.c.kind == "gmail"))).all())


async def test_every_card_gets_a_quiet_draft_with_a_verified_address() -> None:
    """A new card gets one quiet draft to an address on the company's own domain."""
    card_id, _ = await setup_card()  # a new card: resume not approved yet
    async with transaction() as conn:
        card = await cards_repo.get(conn, card_id)
    assert card is not None
    job_id = int(card["job_id"])
    found = agent(
        [
            {"email": "jobs@otherfirm.com", "source": "web search (https://otherfirm.com)"},
            {"email": "careers@acme.com", "source": "https://acme.com/careers"},
        ]
    )
    writer = Writer()
    await compose(job_id, writer, auto=True, contacts=found)  # type: ignore[arg-type]
    [draft] = await rows(job_id)
    assert draft["status"] == "prepared" and draft["to_address"] == "careers@acme.com"  # other domain dropped
    assert "why the applicant fits" in writer.prompts[0].user.lower()  # the fit lines help choose facts
    assert draft["subject"].startswith("Application for") and draft["body"].startswith(
        "Dear Acme Hiring Team,"
    )
    assert await gmail_rows() == 0  # quiet: no Gmail draft, no preview message yet
    await compose(job_id, writer, auto=True, contacts=found)  # type: ignore[arg-type]
    assert len(await rows(job_id)) == 1 and writer.calls == 1  # one draft per job


async def test_no_address_is_said_clearly_and_the_draft_waits_for_one() -> None:
    """With no address the draft waits; a typed address reuses its text."""
    _, job_id, _ = await _applied_card()
    writer = Writer()
    await compose(job_id, writer, auto=True, contacts=agent([]))  # type: ignore[arg-type]
    [draft] = await rows(job_id)
    assert draft["status"] == "needs_address" and draft["to_address"] == ""
    assert draft["to_source"] == "no hiring email found for Acme"
    with pytest.raises(ValueError, match="found no hiring email for Acme"):
        await compose(job_id, writer, contacts=agent([]))  # type: ignore[arg-type]
    await compose(job_id, writer, address_override="hr@acme.com")  # type: ignore[arg-type]
    drafts = await rows(job_id)
    assert drafts[-1]["to_address"] == "hr@acme.com" and drafts[-1]["body"] == drafts[0]["body"]
    assert writer.calls == 1  # the prepared text was reused, not rewritten
    assert await gmail_rows() == 1  # shown now: Gmail draft requested


async def test_email_hr_shows_the_prepared_draft_and_send_still_needs_your_tap() -> None:
    """Email HR shows the prepared draft; sending still needs the Send tap."""
    card_id, job_id, _ = await _applied_card()
    writer = Writer()
    await compose(
        job_id, writer, auto=True, contacts=agent([{"email": "careers@acme.com", "source": "acme.com"}])
    )  # type: ignore[arg-type]
    [prepared] = await rows(job_id)
    with pytest.raises(ValueError, match="tap Email HR first"):
        await send.approve_and_queue(card_id, int(prepared["id"]))
    email_id = await compose(job_id, writer)  # type: ignore[arg-type]  # the Email HR tap
    assert email_id == prepared["id"] and writer.calls == 1  # shown at once, as written
    [shown] = await rows(job_id)
    assert shown["status"] == "draft" and await gmail_rows() == 1
    async with transaction() as conn:
        sends = (await conn.execute(select(outbox.c.payload).where(outbox.c.kind == "gmail"))).all()
    assert [s.payload["op"] for s in sends] == ["create_draft"]  # no send without the Send tap


async def test_a_card_queues_its_automatic_draft(p2_deps: Any, checkpointer: Any) -> None:
    """Every card a run makes queues its automatic draft."""
    from jobpilot.bot import router
    from jobpilot.db.tables import work_items
    from jobpilot.models.work import EMAIL_DRAFT
    from tests.conftest import telegram_update
    from tests.e2e.test_job_runs import confirmed_profile, drain_work, set_prefs, use_fixture_source

    await confirmed_profile()
    await set_prefs(threshold=20, cards_per_day=2)
    use_fixture_source(p2_deps)
    await router.ingest_update(telegram_update(8901, "/start"))
    await drain_work(checkpointer)
    async with transaction() as conn:
        auto = (
            await conn.execute(select(work_items.c.payload).where(work_items.c.kind == EMAIL_DRAFT))
        ).all()
    assert len(auto) == 2 and all(r.payload["auto"] for r in auto)
