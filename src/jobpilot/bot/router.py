"""Inbound Telegram: store each update as an event (idempotent on update_id) and turn events into work."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from jobpilot.bot import auth, callbacks, commands
from jobpilot.chat import handle as job_chat_handle
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import events as events_repo
from jobpilot.db.session import read, transaction
from jobpilot.models.work import EMAIL_DRAFT, SHARED_JOB, TELEGRAM_EVENT
from jobpilot.queue import events, jobs_queue, outbox
from jobpilot.run.deps import get_deps
from jobpilot.safety import idempotency
from jobpilot.sources.shared_links import find_url

log = logging.getLogger(__name__)

WORK_KIND = TELEGRAM_EVENT
EMAIL_ONLY = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")


@dataclass(frozen=True)
class Parsed:
    """The kind of a Telegram update, its chat id and its text."""

    type: str  # command | message | tap | other
    chat_id: int | None
    text: str


def parse(update: dict[str, Any]) -> Parsed:
    """Classify a Telegram update as a command, message, tap or other."""
    if "callback_query" in update:
        cq = update["callback_query"]
        chat = (cq.get("message") or {}).get("chat") or {}
        return Parsed("tap", chat.get("id"), cq.get("data", ""))
    msg = update.get("message") or update.get("edited_message")
    if msg:
        text = msg.get("text") or msg.get("caption") or ""
        kind = "command" if text.startswith("/") else "message"
        return Parsed(kind, (msg.get("chat") or {}).get("id"), text)
    return Parsed("other", None, "")


async def ingest_update(update: dict[str, Any]) -> int | None:
    """Store an update and enqueue its work in one transaction. Returns the event id, or None if the
    update was a repeat or came from a chat other than the owner's."""
    update_id = int(update["update_id"])
    parsed = parse(update)
    if parsed.type == "other" or not auth.is_owner(parsed.chat_id):
        return None
    async with transaction() as conn:
        event_id = await events.append(
            conn,
            type=f"telegram.{parsed.type}",
            payload=update,
            idempotency_key=idempotency.telegram_update(update_id),
            telegram_update_id=update_id,
        )
        if event_id is None:
            return None
        await jobs_queue.enqueue(
            conn,
            kind=WORK_KIND,
            payload={"event_id": event_id},
            idempotency_key=idempotency.work_for_event(event_id, WORK_KIND),
        )
        # A tap's spinner is stopped by the poller as it arrives; the result arrives as the card's
        # own update or a short message.
    return event_id


async def handle_event(event_id: int) -> None:
    """Work-item handler: route a stored event to a command, a tap handler or Job Chat."""
    async with read() as conn:
        event = await events_repo.get(conn, event_id)
    if event is None:
        log.error("event %s not found", event_id)
        return
    parsed = parse(event["payload"])
    if parsed.type == "command":
        if parsed.text.split()[0].split("@", 1)[0].lower() == "/chat":
            message = event["payload"].get("message") or {}
            reply = message.get("reply_to_message") or {}
            await job_chat_handle(
                event_id,
                parsed.text,
                get_deps().llm,
                reply_to_message_id=reply.get("message_id"),
            )
        else:
            await commands.handle(event_id, parsed.text)
    elif parsed.type == "tap":
        await callbacks.handle(event_id, event["payload"]["callback_query"])
    elif parsed.type == "message":
        message = event["payload"].get("message") or event["payload"].get("edited_message") or {}
        reply = message.get("reply_to_message") or {}
        if await _email_address_reply(event_id, parsed.text, reply.get("message_id")):
            return
        if find_url(parsed.text):
            async with transaction() as conn:
                await jobs_queue.enqueue(
                    conn,
                    kind=SHARED_JOB,
                    payload={"event_id": event_id},
                    idempotency_key=idempotency.work_for_event(event_id, SHARED_JOB),
                )
        else:
            await job_chat_handle(
                event_id,
                parsed.text,
                get_deps().llm,
                reply_to_message_id=reply.get("message_id"),
            )


async def _email_address_reply(event_id: int, text: str, reply_to_message_id: int | None) -> bool:
    """A reply to a card that is just an email address (after "no hiring email found") drafts the HR email
    to that address. Anything else goes on to Job Chat."""
    if reply_to_message_id is None or len(text) > 200:
        return False
    addresses = EMAIL_ONLY.findall(text.strip())
    if len(addresses) != 1 or len(text.split()) > 3:
        return False
    async with read() as conn:
        card = await cards_repo.by_telegram_message(conn, reply_to_message_id)
    if card is None:
        return False
    async with transaction() as conn:
        await jobs_queue.enqueue(
            conn,
            kind=EMAIL_DRAFT,
            payload={"job_id": int(card["job_id"]), "address": addresses[0], "nonce": event_id},
            idempotency_key=idempotency.work_for_event(event_id, EMAIL_DRAFT),
        )
        await outbox.telegram_message(
            conn,
            f"Drafting the email to {addresses[0]} - the preview follows in a moment.",
            idempotency_key=idempotency.outbox("email-address", event_id),
        )
    return True
