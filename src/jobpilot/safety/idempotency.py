"""Idempotency keys for events, taps and outbox rows. Each is backed by a UNIQUE constraint."""

from __future__ import annotations

import hashlib


def telegram_update(update_id: int) -> str:
    """Return the event key for a Telegram update id."""
    return f"tg-update:{update_id}"


def work_for_event(event_id: int, kind: str) -> str:
    """Return the work item key for the ``kind`` work caused by an event."""
    return f"work:{kind}:event:{event_id}"


def outbox(*parts: object) -> str:
    """A deterministic key from the parts that make a message unique (e.g. 'run', 12, 'summary')."""
    raw = ":".join(str(p) for p in parts)
    if len(raw) <= 200:
        return f"out:{raw}"
    return "out:sha256:" + hashlib.sha256(raw.encode()).hexdigest()
