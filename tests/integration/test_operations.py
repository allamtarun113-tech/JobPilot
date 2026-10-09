"""Email caps, reconfirmation, alerts, login, metrics and other day-to-day operations."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, update

from jobpilot.cards import reminders
from jobpilot.chat import job_resolver
from jobpilot.core import clock
from jobpilot.core.config import get_config
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import emails as emails_repo
from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.queries import queue as queue_repo
from jobpilot.db.queries import runs as runs_repo
from jobpilot.db.session import transaction
from jobpilot.db.tables import cards, events, outbox
from jobpilot.dispatcher.delivery import Dispatcher
from jobpilot.hr_email import send
from jobpilot.models.card import CardStatus
from jobpilot.monitoring import health, metrics
from jobpilot.queue import outbox as outbox_q
from jobpilot.queue import reconfirm
from jobpilot.run import lease
from jobpilot.safety import totp
from jobpilot.sources import dedup
from jobpilot.sources.shared_links import posting_from_share
from jobpilot.web import login_guard
from jobpilot.web.main import create_app
from tests.conftest import FakeSender
from tests.integration.test_card_transitions import setup_card
from tests.integration.test_email_dispatch import _draft_ready

pytestmark = pytest.mark.usefixtures("db")


def dispatcher(sender: FakeSender, shadow: bool = False) -> Dispatcher:
    """Build a dispatcher (shadow mode off by default) on a fake sender."""
    return Dispatcher({"telegram": sender, "gmail": sender}, get_config().dispatcher, shadow_mode=shadow)


async def _status(key_part: str) -> list[str]:
    """Return the statuses of outbox rows whose key contains ``key_part``."""
    async with transaction() as conn:
        rows = await conn.execute(select(outbox.c.idempotency_key, outbox.c.status))
        return [s for k, s in rows.all() if key_part in k]


async def test_email_cap_defers_send_without_killing_it() -> None:
    """A send over the daily limit is deferred, not marked dead or attempted."""
    card_id, email_id = await _draft_ready()
    await send.approve_and_queue(card_id, email_id)
    get_config().caps.emails_per_day = 0  # a daily limit set in config (off by default, D4)
    await dispatcher(FakeSender()).deliver_ready()
    async with transaction() as conn:
        row = (
            (await conn.execute(select(outbox).where(outbox.c.payload["op"].as_string() == "send_draft")))
            .mappings()
            .one()
        )
    assert row["status"] == "pending" and row["attempts"] == 0 and row["next_attempt_at"] > clock.now()


async def test_skip_cancels_an_approved_send() -> None:
    """Skipping an approved email cancels its queued send."""
    card_id, email_id = await _draft_ready()
    await send.approve_and_queue(card_id, email_id)
    assert "nothing will be sent" in await send.skip(card_id, email_id)
    gmail = FakeSender()
    await dispatcher(gmail).deliver_ready()
    assert not [c for c in gmail.calls if c.get("op") == "send_draft"]
    async with transaction() as conn:
        card = await cards_repo.get(conn, card_id)
        email = await emails_repo.get(conn, email_id)
    assert card["status"] == CardStatus.APPLIED.value and email["status"] == "skipped"


async def test_sweep_asks_for_reconfirmation_after_24_hours(frozen_clock) -> None:
    """The sweep expires a 25-hour-old approval and asks for a fresh tap."""
    card_id, email_id = await _draft_ready()
    await send.approve_and_queue(card_id, email_id)
    frozen_clock.advance(hours=25)
    async with transaction() as conn:
        assert await reconfirm.sweep(conn) == 1
        card = await cards_repo.get(conn, card_id)
    assert card["status"] == CardStatus.NEEDS_RECONFIRM.value
    assert await _status("send") == ["skipped"]
    assert await _status("out:reconfirm:") == ["pending"]  # the re-approve message (plus an alert)


async def test_dead_rows_and_stuck_runs_raise_alerts(frozen_clock) -> None:
    """A run with no progress for 30 minutes raises the stuck-run alert."""
    async with transaction() as conn:
        await outbox_q.telegram_message(conn, "x", idempotency_key="k", partition_key="card:9")
        run = await runs_repo.create(conn, holder="w", card_cap=1)
    await dispatcher(FakeSender(failures=[True], retryable=False)).deliver_ready()
    frozen_clock.advance(minutes=30)
    async with transaction() as conn:
        await conn.execute(
            update(runs_repo.runs)
            .where(runs_repo.runs.c.id == run["id"])
            .values(updated_at=clock.now() - timedelta(minutes=30))
        )
        raised = await health.check(conn)
    assert "stuck-run" in raised


async def test_paused_run_continues_as_the_same_run() -> None:
    """/resume continues the paused run itself; a second /resume is held."""
    async with transaction() as conn:
        run = await runs_repo.create(conn, holder="w1", card_cap=3)
        assert await runs_repo.mark_paused(conn, run["id"], "w1", {"jobs_api": {"fetched": 2}})
    nothing = await lease.acquire("w2", 5, "r1", resume_only=True)
    assert isinstance(nothing, lease.Acquired) and nothing.run_id == run["id"] and nothing.from_pause
    assert isinstance(await lease.acquire("w3", 5, "r2", resume_only=True), lease.Held)


async def test_shared_jobs_without_company_do_not_merge_and_requeue_resets() -> None:
    """Shares without a company stay separate; re-queuing a served job re-serves it."""
    a = posting_from_share("Python Developer\nhttps://x.example/1", 1)
    b = posting_from_share("Python Developer\nhttps://y.example/2", 2)
    assert a and b
    async with transaction() as conn:
        first = await dedup.ingest(conn, [a], None)
        second = await dedup.ingest(conn, [b], None)
        assert first[0].job_id != second[0].job_id
        now = clock.now()
        await queue_repo.upsert(conn, first[0].job_id, 80, now, now + timedelta(days=10))
        await queue_repo.mark_served(conn, first[0].job_id, now)
        await queue_repo.upsert(conn, first[0].job_id, 85, now, now + timedelta(days=10))
        assert [r["job_id"] for r in await queue_repo.waiting(conn, now)] == [first[0].job_id]


async def test_login_lockout_and_replay(monkeypatch) -> None:
    """A reused code is refused, repeated failures lock login, /health stays bare."""
    secret = totp.new_secret()
    monkeypatch.setenv("DASHBOARD_TOTP_SECRET", secret)
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET", "s" * 32)
    from jobpilot.core.config import get_settings

    get_settings.cache_clear()
    try:
        async with transaction() as conn:
            counter = int(clock.now().timestamp()) // 30
            assert await login_guard.accept_counter(conn, counter)
            assert not await login_guard.accept_counter(conn, counter)  # replay refused
            for _ in range(get_config().login.max_failures):
                await login_guard.record_failure(conn)
            with pytest.raises(login_guard.LockedOut):
                await login_guard.check_allowed(conn)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://127.0.0.1"
        ) as c:
            health_body = (await c.get("/health")).json()
        assert health_body == {"ok": True}
    finally:
        get_settings.cache_clear()


async def test_metrics_strip_and_digest() -> None:
    """The digest lists a card idle for 3 days; the metrics strip works empty."""
    card_id, _ = await setup_card()
    async with transaction() as conn:
        card = await cards_repo.get(conn, card_id)
        await conn.execute(
            update(cards).where(cards.c.id == card_id).values(updated_at=clock.now() - timedelta(days=3))
        )
        text, markup = await reminders.digest(conn)
        strip = await metrics.strip(conn)
    assert "Cards waiting for you" in text and "Acme" in text and markup
    assert strip["approval"].total == 0 and strip["reply"].total == 0 and card is not None


async def test_job_resolver_never_guesses_from_single_words() -> None:
    """Ordinary words don't pick a job; the whole company name does."""
    await setup_card()
    async with transaction() as conn:
        await jobs_repo.update(conn, 1, 1, company="MakeMyTrip", company_norm="makemytrip")
        guess = await job_resolver.resolve(conn, "make my resume shorter")
        named = await job_resolver.resolve(conn, "/chat makemytrip why this score")
    assert guess.job_id is None and guess.choices
    assert named.job_id == 1


async def test_old_event_payloads_are_redacted(frozen_clock) -> None:
    """Event payloads older than the retention window are redacted once."""
    async with transaction() as conn:
        await conn.execute(
            events.insert().values(type="t", payload={"text": "mail a@b.com"}, idempotency_key="e1")
        )
    frozen_clock.advance(days=200)
    async with transaction() as conn:
        assert await health.redact_old_events(conn) == 1
        payload = (await conn.execute(select(events.c.payload))).scalar_one()
    assert payload == {"text": "mail [email]", "redacted": True}


async def test_jobs_page_opens_a_job_in_the_drawer() -> None:
    """An HTMX request gets the drawer; a normal one the full page."""
    await setup_card()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://127.0.0.1"
    ) as c:
        page = await c.get("/jobs")
        drawer = await c.get("/jobs/1", headers={"HX-Request": "true"})
        full = await c.get("/jobs/1")
    assert 'hx-get="/jobs/1"' in page.text and 'id="drawer"' in page.text
    assert "Open full page" in drawer.text and "<html" not in drawer.text and "Match overview" in drawer.text
    assert "<html" in full.text and "Back to jobs" in full.text


async def test_below_bar_jobs_join_the_queue_when_the_bar_drops() -> None:
    """A lower threshold promotes stored scores above it, with no model call."""
    from jobpilot.db.queries import requirements
    from jobpilot.db.queries import scores as scores_repo
    from jobpilot.models.job import PipelineStatus
    from jobpilot.models.requirements import Requirements, Seniority
    from jobpilot.run.steps.serve_queue import _promote_now_above_bar

    async with transaction() as conn:
        ids = []
        for key, score in (("t:67", 67.2), ("t:60", 60.3)):
            job_id = await jobs_repo.insert_job(
                conn,
                canonical_key=key,
                company="Acme",
                company_norm="acme",
                title="Data Analyst",
                title_norm="data analyst",
                city="pune",
                apply_url="https://x.example",
                jd_text="SQL role",
                jd_is_snippet=False,
                pipeline_status=PipelineStatus.BELOW_BAR.value,
            )
            req_id = await requirements.store(
                conn,
                jd_hash=key,
                data=Requirements(
                    must_skills=["SQL"],
                    nice_skills=[],
                    seniority=Seniority.ENTRY,
                    domain="analytics",
                    title_norm="data analyst",
                    years_min=None,
                    years_max=None,
                    is_job_description=True,
                ).model_dump(mode="json"),
                prompt_version="test",
                model_id="test",
            )
            await scores_repo.add(
                conn,
                job_id=job_id,
                requirements_id=req_id,
                stage_a=0.9,
                score=score,
                parts={},
                matched=[],
                gap=[],
                rubric_version=1,
                profile_hash="x",
            )
            ids.append(job_id)
        promoted = await _promote_now_above_bar(conn, 65, clock.now())
        waiting = {r["job_id"] for r in await queue_repo.waiting(conn, clock.now())}
        statuses = [(await jobs_repo.get(conn, i))["pipeline_status"] for i in ids]
    assert promoted == 1 and waiting == {ids[0]}
    assert statuses == [PipelineStatus.QUEUED.value, PipelineStatus.BELOW_BAR.value]
