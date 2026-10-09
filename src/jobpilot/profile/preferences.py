"""Preferences: locations, experience band, titles, lists, threshold, caps - versioned.

Changed from the dashboard form or /prefs on Telegram (both user-typed; Job Chat can only propose changes).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core.config import get_config
from jobpilot.db.queries import preferences as repo
from jobpilot.models.preferences import Preferences


def defaults() -> Preferences:
    """Return the default preferences (threshold and card cap from config)."""
    cfg = get_config()
    return Preferences(
        threshold=cfg.match_threshold,
        cards_per_day=cfg.caps.cards_per_day,
    )


async def load(conn: AsyncConnection) -> tuple[Preferences, int]:
    """Return the preferences and their version, creating defaults if missing."""
    row = await repo.ensure(conn, defaults().model_dump(mode="json"))
    return Preferences.model_validate(row["data"]), row["version"]


async def save(conn: AsyncConnection, prefs: Preferences, expected_version: int) -> int:
    """Version-checked save of the preferences.

    Returns:
        The new version.
    """
    return await repo.save(conn, expected_version, prefs.model_dump(mode="json"))


@dataclass(frozen=True)
class Change:
    """One preference change, for the confirmation message."""

    field: str
    before: object
    after: object


def apply_command(prefs: Preferences, args: list[str]) -> tuple[Preferences, Change]:
    """Parse '/prefs <what> <value>' and return the new preferences plus the change made.

    threshold 75 | cards 8 | company on|off | block <company> | unblock <company> |
    location <city> | unlocation <city> | remote on|off
    """
    if len(args) < 2:
        raise ValueError("usage")
    what, value = args[0].lower(), " ".join(args[1:]).strip()
    new = prefs.model_copy(deep=True)
    if what == "threshold":
        new.threshold = int(value)
        if not 0 <= new.threshold <= 100:
            raise ValueError("threshold must be 0-100")
        return new, Change("match threshold", prefs.threshold, new.threshold)
    if what in ("cards", "cap"):
        new.cards_per_day = int(value)
        if not 1 <= new.cards_per_day <= 50:
            raise ValueError("cards per day must be 1-50")
        return new, Change("cards per day", prefs.cards_per_day, new.cards_per_day)
    if what == "company":
        new.company_rule = value.lower() in ("on", "yes", "true", "1")
        return new, Change("one email per company per 30 days", prefs.company_rule, new.company_rule)
    if what in ("block", "unblock"):
        before = list(prefs.blacklist)
        lowered = [c.lower() for c in new.blacklist]
        if what == "block" and value.lower() not in lowered:
            new.blacklist.append(value)
        if what == "unblock":
            new.blacklist = [c for c in new.blacklist if c.lower() != value.lower()]
        return new, Change("blocked companies", before, new.blacklist)
    if what in ("location", "unlocation"):
        before = list(prefs.locations)
        if what == "location" and value.lower() not in [c.lower() for c in new.locations]:
            new.locations.append(value)
        if what == "unlocation":
            new.locations = [c for c in new.locations if c.lower() != value.lower()]
        return new, Change("locations", before, new.locations)
    if what == "digest":
        new.digest_mode = value.lower() in ("on", "yes", "true", "1")
        return new, Change("digest mode", prefs.digest_mode, new.digest_mode)
    if what == "remote":
        new.remote_ok = value.lower() in ("on", "yes", "true", "1")
        return new, Change("remote ok", prefs.remote_ok, new.remote_ok)
    raise ValueError("usage")
