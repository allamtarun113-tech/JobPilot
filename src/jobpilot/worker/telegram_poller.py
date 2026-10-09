"""Reads the owner's messages and taps by long polling (getUpdates with an offset).

Each update is stored as an event keyed by update_id, so a repeat is a no-op. Nothing listens on the
internet. Disabled while TELEGRAM_BOT_TOKEN is empty (use `make fake-update` to simulate messages).

A tap's button spinner is stopped here, straight away and in parallel with storing the tap: the
acknowledgement changes nothing in the chat, so it doesn't need the outbox's exactly-once delivery, and the
round trips to the database would otherwise keep the spinner turning for seconds.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from jobpilot.bot import auth, router
from jobpilot.core.config import get_config, get_settings
from jobpilot.db.queries import events as events_repo
from jobpilot.db.session import transaction

log = logging.getLogger(__name__)
API = "https://api.telegram.org"
ACK_TEXT = "Working on it..."


async def _acknowledge(client: httpx.AsyncClient, token: str, update: dict) -> None:
    """Best effort: stop the owner's button spinner at once. A failure only leaves the spinner running."""
    cq = update.get("callback_query") or {}
    chat_id = ((cq.get("message") or {}).get("chat") or {}).get("id")
    if not cq.get("id") or not auth.is_owner(chat_id):
        return
    try:
        await client.post(
            f"{API}/bot{token}/answerCallbackQuery",
            json={"callback_query_id": str(cq["id"]), "text": ACK_TEXT},
            timeout=10,
        )
    except httpx.HTTPError as exc:
        log.info("tap acknowledgement failed (%s)", type(exc).__name__)


async def run() -> None:
    """Long-poll Telegram for updates and store each one as an event.

    Resumes from the highest stored update id, so nothing is read
    twice after a restart. Disabled without a bot token.
    """
    settings = get_settings()
    if settings.telegram_dry_run:
        log.info("TELEGRAM_BOT_TOKEN not set: poller disabled (simulate with `make fake-update TEXT=/start`)")
        return
    token = settings.telegram_bot_token.get_secret_value()  # type: ignore[union-attr]
    timeout = get_config().telegram.long_poll_timeout_seconds
    async with transaction() as conn:
        last = await events_repo.max_telegram_update_id(conn)
    offset = (last + 1) if last is not None else None
    backoff = 1.0
    async with httpx.AsyncClient(timeout=timeout + 15) as client:
        while True:
            try:
                params: dict[str, object] = {
                    "timeout": timeout,
                    "allowed_updates": ["message", "callback_query"],
                }
                if offset is not None:
                    params["offset"] = offset
                resp = await client.post(f"{API}/bot{token}/getUpdates", json=params)
                body = resp.json()
                if not body.get("ok"):
                    raise RuntimeError(f"getUpdates failed: {body.get('description')}")
                for update in body["result"]:
                    # Stop the button spinner while the tap is being
                    # stored.
                    ack = asyncio.create_task(_acknowledge(client, token, update))
                    await router.ingest_update(update)
                    await ack
                    # Advance only after the update is stored: a crash
                    # re-reads it.
                    offset = int(update["update_id"]) + 1
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except httpx.TimeoutException:
                # A long poll that outlives the client timeout (a laptop waking, a dropped connection): ask
                # again at once - backing off here left taps unanswered for up to a minute.
                log.info("long poll timed out; polling again")
            except Exception as exc:
                log.warning("poller error (%s); retrying in %.0fs", type(exc).__name__, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
