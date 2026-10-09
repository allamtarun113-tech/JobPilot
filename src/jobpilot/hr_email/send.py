"""Send vN and Skip for an HR email.

Send vN: durable approval + card -> sending + tokenless outbox row, in
one transaction. Skip: cancels the draft and any queued send for it, in
one transaction.
"""

from __future__ import annotations

from jobpilot.core.config import get_config
from jobpilot.db.queries import approvals, emails
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import outbox as outbox_repo
from jobpilot.db.session import transaction
from jobpilot.models.card import CardStatus
from jobpilot.queue import outbox
from jobpilot.safety import caps, idempotency


async def approve_and_queue(card_id: int, email_id: int) -> int:
    """Approve an exact email version and queue its send, in one transaction.

    The outbox row holds no token; the dispatcher mints one from this
    approval at delivery time.

    Args:
        card_id: The job's card.
        email_id: The email version the user tapped Send on.

    Returns:
        The approval id.

    Raises:
        ValueError: Any rule blocks the send (already sent, stale draft,
            not applied, wrong resume, draft not ready, caps).
    """
    async with transaction() as conn:
        # Serialise with other sends so caps and the one-email rule
        # hold.
        await caps.lock_capacity(conn)
        card, email = await cards_repo.get(conn, card_id), await emails.get(conn, email_id)
        if card is None or email is None or email["job_id"] != card["job_id"]:
            raise ValueError("card/email mismatch")
        if await emails.sent_for_job(conn, card["job_id"]):
            raise ValueError("an email was already sent for this job")
        if card["status"] not in {CardStatus.APPLIED.value, CardStatus.NEEDS_RECONFIRM.value}:
            raise ValueError("mark the job applied first")
        latest = await emails.latest(conn, card["job_id"])
        if latest is None or latest["id"] != email_id:
            raise ValueError(
                f"This draft changed - the current email is v{latest['version'] if latest else '?'}."
            )
        if email["status"] in {"skipped", "sent"}:
            raise ValueError(f"this email draft is already {email['status']}")
        if email["status"] in {"prepared", "needs_address"}:
            raise ValueError("tap Email HR first to see this draft")
        if email["resume_version_id"] != card["approved_resume_version_id"]:
            raise ValueError("email does not use the approved resume version")
        draft_id = email["gmail_draft_id"]
        if not draft_id:
            raise ValueError("Gmail draft is not ready yet - try again in a moment")
        if str(draft_id).startswith("shadow-draft:") and not get_config().shadow_mode:
            raise ValueError("this draft was made in shadow mode; ask for a fresh email draft")
        if card["status"] == CardStatus.NEEDS_RECONFIRM.value:
            # Re-approval: the old approval's queued send must never go
            # out.
            await outbox_repo.supersede_email_sends(conn, email_id)
        await caps.check_email_capacity(conn, card["job_id"])
        approval_id = await approvals.create(
            conn, job_id=card["job_id"], kind="email", target_id=email_id, target_version=email["version"]
        )
        await cards_repo.update(conn, card_id, card["version"], status=CardStatus.SENDING.value)
        await emails.set_status(conn, email_id, "sending", email["lock_version"])
        await outbox.enqueue(
            conn,
            partition_key=outbox.card_partition(card_id),
            kind="gmail",
            payload={
                "op": "send_draft",
                "approval_id": approval_id,
                "email_id": email_id,
                "job_id": card["job_id"],
                "draft_id": draft_id,
                "thread_id": email["gmail_thread_id"],
                "version": email["version"],
            },
            idempotency_key=idempotency.outbox("email", email_id, "send", approval_id),
        )
        return approval_id


async def skip(card_id: int, email_id: int) -> str:
    """Don't send this email: mark it skipped and cancel any queued send (the card goes back to applied)."""
    async with transaction() as conn:
        card, email = await cards_repo.get(conn, card_id), await emails.get(conn, email_id)
        if card is None or email is None or email["job_id"] != card["job_id"]:
            raise ValueError("card/email mismatch")
        if email["status"] == "sent" or await emails.sent_for_job(conn, card["job_id"]):
            raise ValueError("this email was already sent")
        if email["status"] == "skipped":
            return "Email already skipped."
        await outbox_repo.supersede_email_sends(conn, email_id, reason="skipped by you")
        await emails.set_status(conn, email_id, "skipped", email["lock_version"])
        if card["status"] in {CardStatus.SENDING.value, CardStatus.NEEDS_RECONFIRM.value}:
            await cards_repo.update(conn, card_id, card["version"], status=CardStatus.APPLIED.value)
        return "Email skipped; nothing will be sent."
