"""Retries, run takeover, approvals and daily capacity under concurrency."""

import asyncio
import re
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, update

from jobpilot.chat import resume_edit
from jobpilot.core.config import get_config, get_settings
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import emails as emails_repo
from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.queries import resume_versions as versions_repo
from jobpilot.db.session import transaction
from jobpilot.db.tables import cards, outbox, runs, work_items
from jobpilot.dispatcher.delivery import Dispatcher
from jobpilot.hr_email import send
from jobpilot.models.resume_doc import ResumeDoc
from jobpilot.models.resume_ops import HideBullet
from jobpilot.profile import preferences
from jobpilot.run import graph, lease
from jobpilot.run.steps import serve_queue as sqm
from jobpilot.safety import caps
from jobpilot.web.main import create_app
from tests.conftest import FakeSender, confirmed_profile
from tests.integration.test_card_transitions import setup_card
from tests.integration.test_email_dispatch import _draft_ready

pytestmark = pytest.mark.usefixtures("db")


async def new_job(conn, company="other"):
    """Insert a minimal job and return its id."""
    return await jobs_repo.insert_job(
        conn,
        canonical_key=uuid4().hex,
        company=company,
        company_norm=company,
        title="Engineer",
        title_norm="engineer",
        apply_url="https://example.com/job",
        jd_text="Python job",
    )


async def extra_ready_email(card_id, company="other"):
    """Create a second applied card with an email ready to send.

    Returns:
        (card id, email id).
    """
    async with transaction() as conn:
        original = await cards_repo.get(conn, card_id)
        resume = await versions_repo.latest(conn, original["job_id"])
        job_id = await new_job(conn, company)
        values = {k: v for k, v in dict(resume).items() if k not in {"id", "created_at", "user_id"}}
        values["job_id"] = job_id
        resume_id = await versions_repo.create(conn, **values)
        other_card = await cards_repo.create(conn, job_id=job_id, callback_token=uuid4().hex)
        await cards_repo.update(conn, other_card, 1, status="applied", approved_resume_version_id=resume_id)
        email_id = await emails_repo.create(
            conn,
            job_id=job_id,
            resume_version_id=resume_id,
            version=1,
            to_address="careers@example.com",
            to_source="fixture",
            subject="Hello",
            body="Hello",
            citations=[],
            gmail_draft_id=uuid4().hex,
        )
    return other_card, email_id


async def test_expired_approval_reconfirms_and_unblocks_partition(frozen_clock):
    """An expired approval is skipped; a fresh one sends exactly once."""
    card_id, email_id = await _draft_ready()
    old = await send.approve_and_queue(card_id, email_id)
    frozen_clock.advance(hours=24)
    gmail = FakeSender()
    dispatcher = Dispatcher({"gmail": gmail}, get_config().dispatcher, shadow_mode=False)
    assert await dispatcher.deliver_ready() == 0
    fresh = await send.approve_and_queue(card_id, email_id)
    assert fresh != old
    assert await dispatcher.deliver_ready() == 1
    assert len(gmail.calls) == 1
    async with transaction() as conn:
        states = (
            (
                await conn.execute(
                    select(outbox.c.status)
                    .where(outbox.c.payload["op"].as_string() == "send_draft")
                    .order_by(outbox.c.id)
                )
            )
            .scalars()
            .all()
        )
    assert states == ["skipped", "delivered"]


async def test_dispatch_rejects_invalidated_resume_binding():
    """A send whose resume approval was reset is skipped at delivery."""
    card_id, email_id = await _draft_ready()
    await send.approve_and_queue(card_id, email_id)
    async with transaction() as conn:
        card = await cards_repo.get(conn, card_id)
        await cards_repo.update(conn, card_id, card["version"], status="new", approved_resume_version_id=None)
    gmail = FakeSender()
    assert await Dispatcher({"gmail": gmail}, get_config().dispatcher, shadow_mode=False).deliver_ready() == 0
    assert not gmail.calls
    async with transaction() as conn:
        assert (
            await conn.scalar(
                select(outbox.c.status).where(outbox.c.payload["op"].as_string() == "send_draft")
            )
            == "skipped"
        )


async def test_reconcile_after_acceptance_precedes_expiration(frozen_clock):
    """An email Gmail already accepted is recorded as sent even after expiry."""
    card_id, email_id = await _draft_ready()
    await send.approve_and_queue(card_id, email_id)
    frozen_clock.advance(hours=25)

    class AcceptedSender(FakeSender):
        """A sender whose Sent search finds the email already sent."""

        async def reconcile(self, payload):
            """Report the email as already sent."""
            return "already-accepted"

    sender = AcceptedSender()
    assert (
        await Dispatcher({"gmail": sender}, get_config().dispatcher, shadow_mode=False).deliver_ready() == 1
    )
    assert sender.calls == []
    async with transaction() as conn:
        email = await emails_repo.get(conn, email_id)
        sent = await emails_repo.sent_for_job(conn, email["job_id"])
    assert sent["gmail_message_id"] == "already-accepted"


async def test_zero_email_limit_prevents_approval():
    """A daily email limit of zero refuses the approval."""
    card_id, email_id = await _draft_ready()
    get_config().caps.emails_per_day = 0  # a daily limit set in config (off by default, D4)
    with pytest.raises(caps.CapReached, match="daily email"):
        await send.approve_and_queue(card_id, email_id)
    async with transaction() as conn:
        assert await conn.scalar(select(cards.c.status).where(cards.c.id == card_id)) == "applied"


async def test_capacity_decrease_is_rechecked_at_dispatch():
    """A limit lowered after approval still blocks the send."""
    card_id, email_id = await _draft_ready()
    await send.approve_and_queue(card_id, email_id)
    get_config().caps.emails_per_day = 0  # a daily limit set in config (off by default, D4)
    sender = FakeSender()
    assert (
        await Dispatcher({"gmail": sender}, get_config().dispatcher, shadow_mode=False).deliver_ready() == 0
    )
    assert sender.calls == []


async def test_concurrent_approvals_reserve_only_available_capacity():
    """Two approvals racing for one slot: one wins, one is refused."""
    card_id, email_id = await _draft_ready()
    second, second_email = await extra_ready_email(card_id)
    get_config().caps.emails_per_day = 1  # a daily limit set in config (off by default, D4)
    results = await asyncio.gather(
        send.approve_and_queue(card_id, email_id),
        send.approve_and_queue(second, second_email),
        return_exceptions=True,
    )
    assert sum(isinstance(result, int) for result in results) == 1
    assert sum(isinstance(result, caps.CapReached) for result in results) == 1


async def test_two_drafts_prepared_before_send_still_obey_company_cooldown():
    """With the company rule on, a second email to the company is refused."""
    card_id, email_id = await _draft_ready()
    async with transaction() as conn:  # the company rule is a setting, off by default (D4)
        prefs, version = await preferences.load(conn)
        await preferences.save(conn, prefs.model_copy(update={"company_rule": True}), version)
    second, second_email = await extra_ready_email(card_id, "acme")
    await send.approve_and_queue(card_id, email_id)
    with pytest.raises(caps.CapReached, match="company"):
        await send.approve_and_queue(second, second_email)
    assert (
        await Dispatcher({"gmail": FakeSender()}, get_config().dispatcher, shadow_mode=False).deliver_ready()
        == 1
    )
    with pytest.raises(caps.CapReached, match="cooldown"):
        await send.approve_and_queue(second, second_email)


async def test_concurrent_card_reservations_enforce_daily_limit_and_expire(frozen_clock):
    """Racing card reservations respect the limit; expired ones free capacity."""
    async with transaction() as conn:
        job_ids = [await new_job(conn) for _ in range(3)]
        prefs, version = await preferences.load(conn)
        await preferences.save(conn, prefs.model_copy(update={"cards_per_day": 1}), version)

    async def reserve(job_id):
        """Reserve capacity for one job in its own transaction."""
        async with transaction() as conn:
            return await caps.reserve_card(conn, job_id)

    results = await asyncio.gather(*(reserve(job_id) for job_id in job_ids), return_exceptions=True)
    assert sum(isinstance(result, str) for result in results) == 1
    index = next(i for i, result in enumerate(results) if isinstance(result, str))
    async with transaction() as conn:
        await caps.release_card(conn, job_ids[index], results[index])
    token = await reserve(job_ids[0])
    frozen_clock.advance(seconds=get_config().lease.work_item_ttl_seconds + 1)
    replacement = await reserve(job_ids[1])
    assert replacement != token
    async with transaction() as conn:
        with pytest.raises(caps.CapReached, match="expired"):
            await caps.check_card_reservation(conn, job_ids[0], token)


async def test_committed_cards_count_across_runs():
    """Cards made earlier today count against the daily limit."""
    await setup_card()
    async with transaction() as conn:
        prefs, version = await preferences.load(conn)
        await preferences.save(conn, prefs.model_copy(update={"cards_per_day": 1}), version)
        another = await new_job(conn)
    async with transaction() as conn:
        with pytest.raises(caps.CapReached, match="daily card"):
            await caps.reserve_card(conn, another)


async def test_real_checkpoint_takeover_finishes_with_new_worker(checkpointer, frozen_clock, p2_deps):
    """A new worker resumes a crashed run from its real checkpoint."""
    await confirmed_profile()
    got = await lease.acquire("old-worker", 5, "first")
    config = {"configurable": {"thread_id": f"run-{got.run_id}"}}
    initial = {
        "run_id": got.run_id,
        "holder": "old-worker",
        "card_cap": 5,
        "pending_job_ids": [],
        "new_job_ids": [],
        "notes": [],
        "cards_made": 0,
        "jobs_processed": 0,
        "consecutive_errors": 0,
        "counts": {},
        "stop_reason": None,
    }
    compiled = graph.build_graph(checkpointer)
    await compiled.ainvoke(initial, config, interrupt_after=["preflight"])
    assert (await compiled.aget_state(config)).next == ("serve_queue",)
    frozen_clock.advance(seconds=600)
    await graph.execute_run_request(
        {"request_key": "takeover"}, holder="new-worker", checkpointer=checkpointer
    )
    async with transaction() as conn:
        run = (await conn.execute(select(runs))).mappings().one()
    assert run["status"] == "finished" and run["lease_holder"] is None
    snapshot = await compiled.aget_state(config)
    assert snapshot.values["holder"] == "new-worker" and not snapshot.next


@pytest.mark.parametrize("trigger", ["stop", "budget", "errors"])
async def test_queue_serving_checks_stop_rules_between_jobs(monkeypatch, trigger):
    """Serving the queue stops between jobs on /stop, budget or errors."""
    got = await lease.acquire("worker", 5, "run")
    monkeypatch.setattr(sqm.queue_rank, "ranked", _returns([(1, 90), (2, 85), (3, 80), (4, 75)]))
    monkeypatch.setattr(
        sqm.context,
        "for_run",
        _returns(SimpleNamespace(deps=SimpleNamespace(llm=object()), prefs=SimpleNamespace(threshold=70))),
    )
    monkeypatch.setattr(sqm, "_promote_now_above_bar", _returns(0))
    monkeypatch.setattr(sqm.queue_repo, "mark_served", _returns(None))
    calls = []

    async def build(*args, **kwargs):
        """Record the build and trigger the stop condition under test."""
        calls.append(args[0])
        if trigger == "stop":
            async with transaction() as conn:
                await conn.execute(update(runs).where(runs.c.id == got.run_id).values(stop_requested=True))
        if trigger == "errors":
            raise ValueError("fixture error")

    async def budget(*args):
        """Report an empty budget after the first build (budget case only)."""
        return 0 if trigger == "budget" and calls else 100

    monkeypatch.setattr(sqm.card_build, "build", build)
    monkeypatch.setattr(sqm.caps, "token_budget_left", budget)
    state = {"run_id": got.run_id, "holder": "worker", "card_cap": 5, "cards_made": 0}
    result = await sqm.serve_queue(state)
    assert len(calls) == (3 if trigger == "errors" else 1)
    assert (
        result["stop_reason"]
        == {"stop": "stop_requested", "budget": "budget", "errors": "error_spike"}[trigger]
    )
    assert graph._after_serve_queue({**state, **result}) == "drain"


def _returns(value):
    """Return an async function that always returns ``value``."""

    async def call(*args, **kwargs):
        """Return the fixed value."""
        return value

    return call


async def test_email_post_requires_csrf_and_enqueues_once():
    """The email form needs CSRF and a double submit queues one draft."""
    card_id, _ = await _draft_ready()
    async with transaction() as conn:
        card = await cards_repo.get(conn, card_id)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://127.0.0.1"
    ) as client:
        page = await client.get("/preferences")
        token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        url = f"/jobs/{card['job_id']}/email"
        assert (await client.post(url, data={"nonce": "same"})).status_code == 403
        form = {"nonce": "same", "csrf": token, "address": "hiring@example.com"}
        assert (await client.post(url, data=form)).status_code == 303
        assert (await client.post(url, data=form)).status_code == 303
    async with transaction() as conn:
        rows = (
            (await conn.execute(select(work_items).where(work_items.c.kind == "email_draft")))
            .mappings()
            .all()
        )
    assert len(rows) == 1 and rows[0]["payload"]["address"] == "hiring@example.com"
    assert get_settings().telegram_dry_run


async def test_concurrent_resume_edits_cannot_overwrite_winning_pdf(monkeypatch, store):
    """Of two racing resume edits one wins and its PDF is the stored one."""
    card_id, _ = await setup_card()
    async with transaction() as conn:
        card = await cards_repo.get(conn, card_id)
        resume = await versions_repo.latest(conn, card["job_id"])
    doc = ResumeDoc.model_validate(resume["doc"])
    bullets = [bullet for section in doc.sections for entry in section.entries for bullet in entry.bullets]

    async def render(changed):
        """Pretend to render slowly, so both edits overlap."""
        await asyncio.sleep(0.05)  # both edits are in flight at once; the job's row lease admits one
        return changed.model_dump_json().encode()

    async def acheck(*args):
        """Pretend the parse check passed."""
        return SimpleNamespace(as_dict=lambda: {"ok": True})

    monkeypatch.setattr(resume_edit.render, "render_pdf", render)
    monkeypatch.setattr(resume_edit.parse_check, "acheck", acheck)
    monkeypatch.setattr(resume_edit, "get_store", lambda: store)
    results = await asyncio.gather(
        *(
            resume_edit.edit(card["job_id"], [HideBullet(op="hide_bullet", bullet_id=bullet.id)])
            for bullet in bullets[:2]
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, int) for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    async with transaction() as conn:
        winner = await versions_repo.latest(conn, card["job_id"])
    assert winner["version"] == 2
    assert (
        await store.get(winner["pdf_key"])
        == ResumeDoc.model_validate(winner["doc"]).model_dump_json().encode()
    )


async def test_resume_edit_refuses_a_sending_card(monkeypatch):
    """A resume edit is refused while the card's email is sending."""
    card_id, email_id = await _draft_ready()
    await send.approve_and_queue(card_id, email_id)
    async with transaction() as conn:
        card = await cards_repo.get(conn, card_id)
        resume = await versions_repo.latest(conn, card["job_id"])
    doc = ResumeDoc.model_validate(resume["doc"])
    bullet = next(b for section in doc.sections for entry in section.entries for b in entry.bullets)
    with pytest.raises(ValueError, match="while sending"):
        await resume_edit.edit(card["job_id"], [HideBullet(op="hide_bullet", bullet_id=bullet.id)])
    async with transaction() as conn:
        assert (
            await conn.scalar(
                select(func.count())
                .select_from(outbox)
                .where(outbox.c.payload["op"].as_string() == "send_draft", outbox.c.status == "pending")
            )
            == 1
        )


async def test_card_capacity_decrease_is_rechecked_before_commit():
    """A card limit lowered mid-build fails the reservation check."""
    await setup_card()
    async with transaction() as conn:
        another = await new_job(conn)
        token = await caps.reserve_card(conn, another)
        prefs, version = await preferences.load(conn)
        await preferences.save(conn, prefs.model_copy(update={"cards_per_day": 1}), version)
    async with transaction() as conn:
        with pytest.raises(caps.CapReached, match="limit changed"):
            await caps.check_card_reservation(conn, another, token)


async def test_by_default_there_is_no_daily_email_limit_and_no_company_rule():
    """By default two emails to the same company can both be approved."""
    card_id, email_id = await _draft_ready()
    second, second_email = await extra_ready_email(card_id, "acme")  # same company as the first
    assert isinstance(await send.approve_and_queue(card_id, email_id), int)
    assert isinstance(await send.approve_and_queue(second, second_email), int)
