"""Exact resume-version approval, stale taps and card state transitions."""

import httpx
import pytest
from sqlalchemy import func, select

from jobpilot.cards import render, transitions
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.queries import requirements, resume_docs
from jobpilot.db.queries import resume_versions as versions_repo
from jobpilot.db.queries import scores as scores_repo
from jobpilot.db.session import transaction
from jobpilot.db.tables import approvals
from jobpilot.models.requirements import Requirements, Seniority
from jobpilot.queue import outbox
from jobpilot.web.main import create_app
from tests.conftest import confirmed_profile

pytestmark = pytest.mark.usefixtures("db")


async def test_job_page_exposes_resume_comparison() -> None:
    """The job page shows the resume comparison and skills to learn."""
    job_id, _ = await setup_card()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://127.0.0.1"
    ) as client:
        response = await client.get(f"/jobs/{job_id}")
    assert response.status_code == 200
    assert "View resume v1 (PDF)" in response.text
    assert "Original vs tailored resume" in response.text
    assert "Resume keyword score" in response.text
    assert "Confirmed-profile job fit" in response.text
    assert "Changes applied" in response.text
    assert "Skills to learn" in response.text


async def setup_card() -> tuple[int, str]:
    """Create a scored, carded job with one resume version and a sent card.

    Returns:
        (card id, the card's button token).
    """
    await confirmed_profile()
    async with transaction() as conn:
        base = await resume_docs.current(conn)
        assert base is not None
        job_id = await jobs_repo.insert_job(
            conn,
            canonical_key="test:card",
            company="Acme",
            company_norm="acme",
            title="Data Analyst",
            title_norm="data analyst",
            city="bengaluru",
            location_raw="Bengaluru",
            remote=False,
            apply_url="https://example.com/apply",
            jd_text="SQL role " * 50,
            jd_is_snippet=False,
            pipeline_status="carded",
        )
        req_id = await requirements.store(
            conn,
            jd_hash="hash",
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
            score=90,
            parts={"core": 35},
            matched=[],
            gap=[],
            rubric_version=1,
            profile_hash="x",
        )
        version_id = await versions_repo.create(
            conn,
            job_id=job_id,
            base_resume_id=base["id"],
            version=1,
            doc=base["doc"],
            operations=[],
            pdf_key="resumes/jobs/1/v1.pdf",
            parse_ok=True,
            parse_report={},
            keyword_coverage={"percent": 100},
            diff=[],
            created_by="tailor",
        )
        assert version_id
        card_id = await cards_repo.create(conn, job_id=job_id, callback_token="opaque-token")
        message = await outbox.telegram_message(
            conn,
            "card",
            idempotency_key="test-card",
            partition_key=outbox.card_partition(card_id),
        )
        await cards_repo.update(conn, card_id, 1, telegram_outbox_id=message)
    return card_id, "opaque-token"


async def test_exact_approval_and_state_order() -> None:
    """Applied needs an approval first; a repeat approval is a no-op."""
    card_id, _ = await setup_card()
    async with transaction() as conn:
        card = await cards_repo.get(conn, card_id)
        assert card is not None
        with pytest.raises(transitions.Refused, match="Approve"):
            await transitions.applied(conn, card, 1)
    async with transaction() as conn:
        card = await cards_repo.get(conn, card_id)
        assert card is not None
        assert await transitions.approve_resume(conn, card, 1, 2) == "Approved resume v1."
    async with transaction() as conn:
        card = await cards_repo.get(conn, card_id)
        assert card is not None
        assert await transitions.approve_resume(conn, card, 1, 3) == "Resume v1 was already approved."
        assert await conn.scalar(select(func.count()).select_from(approvals)) == 1


async def test_stale_resume_tap_is_refused() -> None:
    """Approving an outdated version is refused; button data fits 64 bytes."""
    card_id, token = await setup_card()
    async with transaction() as conn:
        card = await cards_repo.get(conn, card_id)
        base = await resume_docs.current(conn)
        assert card is not None and base is not None
        await versions_repo.create(
            conn,
            job_id=card["job_id"],
            base_resume_id=base["id"],
            version=2,
            doc=base["doc"],
            operations=[],
            pdf_key="resumes/jobs/1/v2.pdf",
            parse_ok=True,
            parse_report={},
            keyword_coverage={"percent": 100},
            diff=[],
            created_by="tailor",
        )
        with pytest.raises(transitions.Refused, match="here is the new one \\(v2\\)"):
            await transitions.approve_resume(conn, card, 1, 4)
    markup = render.buttons(token, 2, "https://example.com/apply")
    callback_values = [button.get("callback_data", "") for row in markup["inline_keyboard"] for button in row]
    assert all(len(value.encode()) <= 64 for value in callback_values)


async def test_a_card_sends_the_html_card_then_the_preview_then_the_pdf() -> None:
    """A card is sent as HTML, then the page-1 preview, then the PDF."""
    from jobpilot.cards import messages
    from jobpilot.db.tables import outbox as outbox_table

    card_id, _ = await setup_card()
    async with transaction() as conn:
        card = await cards_repo.get(conn, card_id)
        assert card is not None
        await cards_repo.update(conn, card_id, card["version"], telegram_outbox_id=None)
        await messages.send_card(conn, card_id)
        rows = (
            await conn.execute(
                select(outbox_table.c.payload)
                .where(outbox_table.c.partition_key == outbox.card_partition(card_id))
                .order_by(outbox_table.c.seq)
            )
        ).all()
    ops = [(r.payload["op"], r.payload.get("parse_mode"), r.payload.get("storage_key")) for r in rows][-3:]
    assert ops == [
        ("send_message", "HTML", None),
        ("send_photo", None, "resumes/jobs/1/v1.png"),
        ("send_document", None, "resumes/jobs/1/v1.pdf"),
    ]
