"""The 24-hour rule for approved email sends.

Shared by the dispatcher (at delivery) and the worker's scheduler sweep
(so you hear about it even while Gmail is down). An approved send not
delivered within the window: the queued send is skipped, the card moves
to needs_reconfirm, and one message with a 'Re-approve and send vN'
button asks for a fresh tap.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core.config import get_config
from jobpilot.db.queries import approvals as approvals_repo
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import emails as emails_repo
from jobpilot.db.queries import outbox as outbox_repo
from jobpilot.db.tables import outbox as outbox_table
from jobpilot.models.card import CardStatus
from jobpilot.monitoring import alerts
from jobpilot.queue import outbox
from jobpilot.safety import approval_window, idempotency


async def expire_send(conn: AsyncConnection, card: Any, email: Any, approval: Any) -> None:
    """Expire one approved send and ask the user to re-approve it.

    Args:
        conn: An open transaction.
        card: The card row (status ``sending``).
        email: The email row being sent.
        approval: The approval that has expired.
    """
    await cards_repo.update(conn, card["id"], card["version"], status=CardStatus.NEEDS_RECONFIRM.value)
    await emails_repo.set_status(conn, email["id"], "needs_reconfirm", email["lock_version"])
    # Skip the queued send so the old approval can never be used.
    await outbox_repo.supersede_email_sends(conn, email["id"], reason="approval expired after 24 hours")
    hours = get_config().outreach["reconfirm_after_hours"]
    await outbox.telegram_message(
        conn,
        f"Email v{email['version']} to {email['to_address']} could not be sent within {hours} hours. "
        "Tap to send it now, or ignore it.",
        partition_key=f"notice:reconfirm:{email['id']}",
        idempotency_key=idempotency.outbox("reconfirm", email["id"], approval["id"]),
        reply_markup={
            "inline_keyboard": [
                [
                    {
                        "text": f"Re-approve and send v{email['version']}",
                        "callback_data": f"es:{card['callback_token']}:{email['id']}",
                    }
                ]
            ]
        },
    )
    await alerts.send(conn, f"reconfirm:{email['id']}", "An approved email was not sent within 24 hours.")


async def sweep(conn: AsyncConnection) -> int:
    """Expire every pending send whose approval is past the window. Returns how many."""
    rows = await conn.execute(
        select(outbox_table).where(
            outbox_table.c.kind == "gmail",
            outbox_table.c.payload["op"].as_string() == "send_draft",
            outbox_table.c.status.in_(("pending", "dead")),
        )
    )
    expired = 0
    for row in rows.mappings().all():
        payload = row["payload"]
        approval = await approvals_repo.get(conn, int(payload.get("approval_id") or 0))
        # Already used, or a send without a known approval: leave it.
        if approval is None or approval["consumed_at"] is not None:
            continue
        if not approval_window.expired(approval["approved_at"]):
            continue
        card = await cards_repo.for_job(conn, int(payload["job_id"]))
        email = await emails_repo.get(conn, int(payload["email_id"]))
        if card is None or email is None or card["status"] != CardStatus.SENDING.value:
            continue
        await expire_send(conn, card, email, approval)
        expired += 1
    return expired
