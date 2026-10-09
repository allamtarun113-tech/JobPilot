"""The card's Telegram messages: the card itself, its edits and the resume PDF, all written to the outbox."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.cards import render as card_render
from jobpilot.core.config import get_settings
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.queries import requirements
from jobpilot.db.queries import resume_versions as versions_repo
from jobpilot.db.queries import scores as scores_repo
from jobpilot.queue import outbox
from jobpilot.resume import diff, preview, review, skill_additions
from jobpilot.safety import idempotency


async def _card_context(conn: AsyncConnection, card: Any) -> tuple[Any, Any, Any, Any]:
    """Load the job, latest score, requirements and newest resume version of a card."""
    job = await jobs_repo.get(conn, card["job_id"])
    score = await scores_repo.latest_for(conn, card["job_id"])
    req = await requirements.get(conn, score["requirements_id"]) if score else None
    latest = await versions_repo.latest(conn, card["job_id"])
    return job, score, req, latest


def _boost(latest: Any, score: Any) -> dict[str, Any]:
    """Return the skills a version added and the match before and after them."""
    return skill_additions.boost(list(latest["operations"] or []), list(score["gap"]), float(score["score"]))


async def send_card(conn: AsyncConnection, card_id: int) -> int:
    """Write the card message and its resume PDF to the card's ordered outbox partition (idempotent)."""
    card = await cards_repo.get(conn, card_id)
    assert card is not None
    job, score, req, latest = await _card_context(conn, card)
    assert job is not None and score is not None and latest is not None
    message_id = await outbox.telegram_message(
        conn,
        card_render.text(
            dict(job),
            dict(score),
            dashboard_url=get_settings().dashboard_base_url,
            requirements=dict(req["data"]) if req else None,
            boost=_boost(latest, score),
        ),
        idempotency_key=idempotency.outbox("card", card_id, "v1"),
        partition_key=outbox.card_partition(card_id),
        reply_markup=card_render.buttons(card["callback_token"], latest["version"], job["apply_url"]),
        parse_mode="HTML",
    )
    if card["telegram_outbox_id"] is None:
        await cards_repo.update(conn, card_id, card["version"], telegram_outbox_id=message_id)
    await send_resume(conn, card_id, job, latest["pdf_key"], latest["version"], latest["id"])
    return message_id


async def edit_card(
    conn: AsyncConnection, card_id: int, note: str, key: object, *, more: bool = False
) -> None:
    """Edit the card message in place (resolved to its Telegram id at delivery time)."""
    card = await cards_repo.get(conn, card_id)
    if card is None or card["telegram_outbox_id"] is None:
        return  # digest mode: the card was never sent; nothing to edit
    job, score, req, latest = await _card_context(conn, card)
    assert job is not None and score is not None and latest is not None
    markup = (card_render.more_buttons if more else card_render.buttons)(
        card["callback_token"], latest["version"], job["apply_url"]
    )
    await outbox.telegram_edit(
        conn,
        card_render.text(
            dict(job),
            dict(score),
            dashboard_url=get_settings().dashboard_base_url,
            requirements=dict(req["data"]) if req else None,
            note=note or None,
            boost=_boost(latest, score),
        ),
        target_outbox_id=card["telegram_outbox_id"],
        idempotency_key=idempotency.outbox("card", card_id, "edit", key),
        partition_key=outbox.card_partition(card_id),
        reply_markup=markup,
        parse_mode="HTML",
    )


async def send_resume(
    conn: AsyncConnection,
    card_id: int,
    job: Any,
    key: str,
    version: int,
    version_id: int,
    *,
    idempotency_key: str | None = None,
) -> None:
    """Queue the resume preview image, then the PDF, for a card.

    Args:
        conn: An open transaction.
        card_id: The card the messages belong to.
        job: The job row.
        key: The PDF's storage key.
        version: The resume version number shown.
        version_id: The resume version row id.
        idempotency_key: Overrides the default key (to send again).
    """
    row = await versions_repo.get(conn, version_id)
    score = await scores_repo.latest_for(conn, job["id"])
    caption = f"Tailored resume v{version} - {job['company'] or 'Unknown company'} / {job['title']}"
    if row is not None:
        caption += f"\nChanges: {diff.summary(list(row['diff'] or []))}"
        if score is not None:
            boost = _boost(row, score)
            caption += f"\nMatch {boost['before']:g}% -> {boost['after']:g}%"
        if row["created_by"] == "tailor":
            caption += "\n" + review.summary((row["parse_report"] or {}).get("review"))
    base_key = idempotency_key or idempotency.outbox("card", card_id, "resume", version_id)
    # Page 1 as an image first, so the resume shows in the chat without opening the PDF (skipped if a
    # version from before previews has none), then the PDF itself.
    await outbox.enqueue(
        conn,
        partition_key=outbox.card_partition(card_id),
        kind="telegram",
        payload={
            "op": "send_photo",
            "storage_key": preview.key_for(key),
            "caption": f"Resume v{version} - page 1",
        },
        idempotency_key=f"{base_key}:preview",
    )
    await outbox.enqueue(
        conn,
        partition_key=outbox.card_partition(card_id),
        kind="telegram",
        payload={"op": "send_document", "storage_key": key, "caption": caption[:1024]},
        idempotency_key=base_key,
    )


async def resend_resume(conn: AsyncConnection, card: Any, event_id: int) -> str:
    """The View resume button: send the newest resume version's PDF again."""
    job = await jobs_repo.get(conn, card["job_id"])
    latest = await versions_repo.latest(conn, card["job_id"])
    if job is None or latest is None:
        return "This job has no resume yet."
    await send_resume(
        conn,
        int(card["id"]),
        job,
        latest["pdf_key"],
        latest["version"],
        latest["id"],
        idempotency_key=idempotency.outbox("resume-view", event_id),
    )
    return f"Sending resume v{latest['version']}."
