"""Simulate a Telegram message from the owner (for use before the bot token exists).

Usage: uv run python scripts/fake_update.py "/start 3"
Stores the update exactly as the poller would; the running worker picks it up.
"""

from __future__ import annotations

import asyncio
import sys
import time

from jobpilot.bot import router
from jobpilot.core.config import get_settings
from jobpilot.db.session import dispose_engine


async def main(text: str) -> None:
    """Store a fake owner message as if the poller had read it.

    Refuses when a real bot token is set: a fake update id would move
    the poller's offset past real messages.
    """
    if not get_settings().telegram_dry_run:
        sys.exit(
            "A real bot token is set: send the message in Telegram instead. "
            "(A simulated update would move the poller's offset past your real messages.)"
        )
    chat_id = get_settings().telegram_chat_id
    if chat_id is None:
        sys.exit("Set TELEGRAM_CHAT_ID in .env first (any number works in dry-run, e.g. 1).")
    update_id = int(time.time() * 1000)
    update = {
        "update_id": update_id,
        "message": {
            "message_id": update_id % 100000,
            "chat": {"id": chat_id},
            "date": int(time.time()),
            "text": text,
        },
    }
    event_id = await router.ingest_update(update)
    print(f"stored event {event_id}" if event_id else "ignored (repeat or not the owner's chat)")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:]) or "/start"))
