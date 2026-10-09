"""Which job a Job Chat message is about.

1. The card you replied to - any message that belongs to it (the card, its resume PDFs, email previews,
   proposals).
2. A company or role named in the message as whole words ("the Swiggy one", "/chat swiggy").
3. The job you chatted about in the last few minutes (config agents.job_chat.recent_job_minutes).
Otherwise, or when several cards match, you pick from 2-3 recent cards. It never guesses from single words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core import clock
from jobpilot.core.config import get_config
from jobpilot.db.queries import cards, chat_messages
from jobpilot.reference import normalize


@dataclass(frozen=True)
class Resolution:
    """The job a message is about, or 2-3 cards to choose from."""

    job_id: int | None
    choices: list[dict]


def _words(text: str) -> str:
    """Return lower-case words padded with spaces, for whole-word matching."""
    return " " + re.sub(r"[^a-z0-9+#]+", " ", text.lower()).strip() + " "


def _named(text: str, rows: list[dict]) -> list[dict]:
    """Cards whose whole normalized company name (or whole title) appears in the text."""
    haystack = _words(text)
    hits = []
    for row in rows:
        company = row.get("company_norm") or normalize.company_norm(str(row.get("company") or ""))
        title = row.get("title_norm") or normalize.title_norm(str(row.get("title") or ""))
        # Short company names ("ai") and one-word titles match too much.
        company_hit = len(company) >= 3 and f" {company} " in haystack
        title_hit = len(title.split()) >= 2 and f" {title} " in _words(normalize.title_norm(text))
        if company_hit or title_hit:
            hits.append(row)
    return hits


async def resolve(conn: AsyncConnection, text: str, *, reply_to_message_id: int | None = None) -> Resolution:
    """Work out which job a Job Chat message is about.

    Args:
        conn: An open connection.
        text: The message (may start with /chat).
        reply_to_message_id: The Telegram message it replies to, if any.

    Returns:
        The job id, or None with recent or matching cards to pick from.
    """
    if reply_to_message_id is not None:
        card = await cards.by_telegram_message(conn, reply_to_message_id)
        if card is not None:
            return Resolution(int(card["job_id"]), [])
    query = text.strip()
    explicit = query.lower().startswith("/chat")
    if explicit:
        query = query[5:].strip()
    recent = await cards.recent_with_names(conn)
    if query:
        # One card per job, even if both its company and title matched.
        matches = list({row["job_id"]: row for row in _named(query, recent)}.values())
        if len(matches) == 1:
            return Resolution(int(matches[0]["job_id"]), [])
        if len(matches) > 1:
            return Resolution(None, matches[:3])
    if not explicit:
        latest = await chat_messages.latest(conn)
        window = timedelta(minutes=get_config().agents["job_chat"].get("recent_job_minutes", 30))
        if latest is not None and latest["created_at"] >= clock.now() - window:
            return Resolution(int(latest["job_id"]), [])
    return Resolution(None, recent[:3])
