"""Button taps: exact-version card transitions, each traced.

Button data (64-byte limit) carries a card's short random token, never a database id alone:
  ra:<tok>:<v>  approve resume vN     ap  I applied        sk  skip        pr  Prepare link
  rv            view the newest resume PDF again
  mo / mb       More... / Back        em  draft HR email   sc  show card (digest mode)
  es:<tok>:<e>  send email            ee  edit email       ek  skip email
  ru:<tok>:<v>  undo resume vN        rc / js  choose this job for Job Chat
  pc / px:<p>   confirm / cancel a Job Chat proposal
"""

from __future__ import annotations

from typing import Any

from opentelemetry import trace

from jobpilot.cards import messages as card_messages
from jobpilot.cards import transitions
from jobpilot.chat import memory as chat_memory
from jobpilot.chat import proposal_service, resume_edit
from jobpilot.core.config import get_settings
from jobpilot.core.errors import VersionConflict
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.session import read, transaction
from jobpilot.hr_email import send as email_send
from jobpilot.models.work import EMAIL_DRAFT
from jobpilot.queue import jobs_queue, outbox
from jobpilot.safety import idempotency

tracer = trace.get_tracer("jobpilot.bot")
CARD_ACTIONS = {"ra", "ap", "sk", "pr", "mo", "mb", "sc", "rv"}
# On success these change the card message itself, so no extra chat message is needed.
SHOWN_ON_CARD = {"ra", "ap", "sk", "mo", "mb", "sc", "rv"}


class Refusal(str):
    """A tap answer that says the action did not happen (it is always shown to you)."""


async def handle(event_id: int, callback_query: dict[str, Any]) -> None:
    """Handle one button tap and answer it.

    Refusals are always sent as a message; successful taps that update
    the card itself need no extra message.

    Args:
        event_id: The stored tap event.
        callback_query: Telegram's callback_query object.
    """
    data = str(callback_query.get("data") or "")
    parts = data.split(":")
    with tracer.start_as_current_span("telegram.tap", attributes={"tap.action": parts[0]}) as span:
        try:
            answer = await _dispatch(event_id, parts)
        except (transitions.Refused, ValueError) as exc:
            answer = Refusal(str(exc))
        except VersionConflict:
            answer = Refusal("That card just changed - please tap again.")
        span.set_attribute("tap.answer", answer[:100])
        if isinstance(answer, Refusal) or parts[0] not in SHOWN_ON_CARD:
            async with transaction() as conn:
                await outbox.telegram_message(
                    conn, answer, idempotency_key=idempotency.outbox("tap-result", event_id)
                )


async def _card(token: str) -> Any:
    """Return the card for a button token.

    Raises:
        ValueError: The card no longer exists.
    """
    async with read() as conn:
        card = await cards_repo.by_token(conn, token)
    if card is None:
        raise ValueError("This card is no longer available.")
    return card


async def _dispatch(event_id: int, parts: list[str]) -> str:
    """Route a tap to its handler by the action code in the button data.

    Returns:
        The answer to show.
    """
    action = parts[0]
    if action in {"pc", "px"} and len(parts) == 2:
        return await proposal_service.decide(parts[1], confirm=action == "pc")
    if action == "ru" and len(parts) == 3:
        card = await _card(parts[1])
        version = await resume_edit.undo(card["job_id"], int(parts[2]))
        return f"Undo created resume v{version}; review it before approving."
    if action in {"rc", "js"} and len(parts) == 2:
        card = await _card(parts[1])
        await chat_memory.remember(card["job_id"], "system", "Job selected for the next message.")
        return "Job selected. Send what you want to change or understand."
    if action in {"em", "es", "ee", "ek"} and len(parts) >= 2:
        return await _email_action(event_id, action, parts)
    if action in CARD_ACTIONS and len(parts) >= 2:
        return await _card_action(event_id, action, parts)
    return "Unknown button."


async def _email_action(event_id: int, action: str, parts: list[str]) -> str:
    """Handle the HR email buttons: draft, send, edit and skip.

    Raises:
        ValueError: The card isn't ready for an email.
    """
    card = await _card(parts[1])
    if action == "em":
        # Searching the web for the address and writing the draft take a while: done as its own work item,
        # so this tap answers at once and the preview (or what's missing) arrives as a message.
        if card["status"] != "applied" or card["approved_resume_version_id"] is None:
            raise ValueError("Approve the resume and tap 'I applied' first, then ask for the HR email.")
        async with transaction() as conn:
            await jobs_queue.enqueue(
                conn,
                kind=EMAIL_DRAFT,
                payload={"job_id": int(card["job_id"]), "nonce": event_id},
                idempotency_key=idempotency.work_for_event(event_id, EMAIL_DRAFT),
            )
        return "Looking up the HR email and drafting it - the preview follows in a moment."
    if len(parts) != 3:
        return "Invalid email action."
    email_id = int(parts[2])
    if action == "es":
        await email_send.approve_and_queue(card["id"], email_id)
        return "Email approved for sending."
    if action == "ek":
        return await email_send.skip(card["id"], email_id)
    dashboard = f"{get_settings().dashboard_base_url.rstrip('/')}/jobs/{card['job_id']}#email"
    return f"Reply to the card with the change you want, or edit it here: {dashboard}"[:200]


async def _card_action(event_id: int, action: str, parts: list[str]) -> str:
    """Run a card button in one transaction; refusals still commit their messages."""
    async with transaction() as conn:
        card = await cards_repo.by_token(conn, parts[1])
        if card is None:
            return "Card not found."
        try:  # a refusal still commits what it wrote (e.g. the stale tap's "here is the new one")
            return await _card_transition(conn, card, event_id, action, parts)
        except transitions.Refused as exc:
            return Refusal(str(exc))


async def _card_transition(conn: Any, card: Any, event_id: int, action: str, parts: list[str]) -> str:
    """Run the card transition named by the tapped button."""
    if action == "ra" and len(parts) == 3:
        return await transitions.approve_resume(conn, card, int(parts[2]), event_id)
    if action == "ap":
        return await transitions.applied(conn, card, event_id)
    if action == "sk":
        return await transitions.skip(conn, card, event_id)
    if action in {"mo", "mb"}:
        return await transitions.toggle_more(conn, card, event_id, more=action == "mo")
    if action == "sc":
        return await transitions.show_card(conn, card)
    if action == "rv":
        await card_messages.resend_resume(conn, card, event_id)
        return ""  # the PDF itself is the answer
    if action == "pr":
        url = f"{get_settings().dashboard_base_url.rstrip('/')}/jobs/{card['job_id']}/prepare"
        await outbox.telegram_message(
            conn,
            f"Prepare for this interview: {url}",
            partition_key=outbox.card_partition(card["id"]),
            idempotency_key=idempotency.outbox("prepare-link", event_id),
        )
        return "Preparation link sent."
    return "Unknown button."
