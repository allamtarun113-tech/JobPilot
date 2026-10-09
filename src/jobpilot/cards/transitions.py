"""Card transitions.

Exact versions, the state machine, optimistic locking, and outbox writes
in one transaction.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.cards import messages, states
from jobpilot.core import clock
from jobpilot.db.queries import approvals
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.queries import resume_versions as versions_repo
from jobpilot.models.card import CardStatus
from jobpilot.queue import outbox
from jobpilot.safety import idempotency


class Refused(ValueError):
    """A tap that can't be carried out; the message says why."""

    pass


async def _move(conn: AsyncConnection, card: Any, after: str, **values: Any) -> None:
    """Move a card to ``after`` (state-checked, version-checked).

    Raises:
        Refused: The state machine doesn't allow the move.
        VersionConflict: The card changed since it was read.
    """
    try:
        states.require(card["status"], after)
    except states.IllegalTransition as exc:
        raise Refused(str(exc)) from exc
    await cards_repo.update(conn, card["id"], card["version"], status=after, **values)


async def _resend_current(conn: AsyncConnection, card: Any, latest: Any, event_id: int) -> None:
    """A stale tap: show the current draft (its PDF with an Approve button for that exact version)."""
    job = await jobs_repo.get(conn, card["job_id"])
    assert job is not None
    await outbox.enqueue(
        conn,
        partition_key=outbox.card_partition(card["id"]),
        kind="telegram",
        payload={
            "op": "send_document",
            "storage_key": latest["pdf_key"],
            "caption": f"This draft changed - here is the current one: resume v{latest['version']}",
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {
                            "text": f"Approve resume v{latest['version']}",
                            "callback_data": f"ra:{card['callback_token']}:{latest['version']}",
                        }
                    ]
                ]
            },
        },
        idempotency_key=idempotency.outbox("card", card["id"], "stale", event_id),
    )


async def approve_resume(conn: AsyncConnection, card: Any, resume_version: int, event_id: int) -> str:
    """Approve exactly resume version ``resume_version`` for a card.

    A tap on an outdated version is refused and the current draft is
    sent instead; it is never guessed.

    Args:
        conn: An open transaction.
        card: The card row.
        resume_version: The version named on the tapped button.
        event_id: The tap event (for idempotent messages).

    Returns:
        The reply to show.

    Raises:
        Refused: No draft yet, or the draft changed.
    """
    latest = await versions_repo.latest(conn, card["job_id"])
    if latest is None:
        raise Refused("This card has no resume draft yet.")
    if latest["version"] != resume_version:
        await _resend_current(conn, card, latest, event_id)
        raise Refused(f"This draft changed - here is the new one (v{latest['version']}).")
    # A repeated tap on the same version is a no-op.
    if (
        card["status"] == CardStatus.RESUME_APPROVED.value
        and card["approved_resume_version_id"] == latest["id"]
    ):
        return f"Resume v{resume_version} was already approved."
    await approvals.create(
        conn, job_id=card["job_id"], kind="resume", target_id=latest["id"], target_version=resume_version
    )
    await _move(conn, card, CardStatus.RESUME_APPROVED.value, approved_resume_version_id=latest["id"])
    await messages.edit_card(conn, card["id"], f"Resume v{resume_version} approved.", event_id)
    return f"Approved resume v{resume_version}."


async def applied(conn: AsyncConnection, card: Any, event_id: int) -> str:
    """Mark a card applied (needs an approved resume).

    Raises:
        Refused: The resume isn't approved yet.
    """
    if card["status"] == CardStatus.APPLIED.value:
        return "Already marked applied."
    if card["status"] != CardStatus.RESUME_APPROVED.value:
        raise Refused("Approve the resume version before marking this job applied.")
    await _move(conn, card, CardStatus.APPLIED.value, applied_at=clock.now())
    await messages.edit_card(conn, card["id"], "Applied.", event_id)
    return "Marked applied."


async def skip(conn: AsyncConnection, card: Any, event_id: int) -> str:
    """Skip a card."""
    if card["status"] == CardStatus.SKIPPED.value:
        return "Already skipped."
    await _move(conn, card, CardStatus.SKIPPED.value)
    await messages.edit_card(conn, card["id"], "Skipped.", event_id)
    return "Skipped."


async def posting_closed(conn: AsyncConnection, card: Any, event_id: int) -> str:
    """Mark a card's posting as closed."""
    if card["status"] == CardStatus.POSTING_CLOSED.value:
        return "Already marked closed."
    await _move(conn, card, CardStatus.POSTING_CLOSED.value)
    await messages.edit_card(conn, card["id"], "Posting closed.", event_id)
    return "Marked as closed."


async def set_outcome(conn: AsyncConnection, card: Any, outcome: str, event_id: int) -> str:
    """Record the outcome of an application (reply, interview, ...).

    Raises:
        Refused: The user hasn't applied yet.
    """
    if not states.outcome_allowed(card["status"], outcome):
        raise Refused("Record an outcome after you have applied.")
    if card["outcome"] == outcome:
        return f"Outcome already {outcome}."
    await cards_repo.update(conn, card["id"], card["version"], outcome=outcome, outcome_at=clock.now())
    await messages.edit_card(conn, card["id"], f"Outcome: {outcome}.", event_id)
    return f"Outcome recorded: {outcome}."


async def toggle_more(conn: AsyncConnection, card: Any, event_id: int, *, more: bool) -> str:
    """Switch the card's buttons to the More menu, or back."""
    await messages.edit_card(conn, card["id"], "", ("more" if more else "less", event_id), more=more)
    return "More options." if more else "Back."


async def show_card(conn: AsyncConnection, card: Any) -> str:
    """Digest mode: send a listed job's full card (once)."""
    await messages.send_card(conn, card["id"])
    return "Card sent."
