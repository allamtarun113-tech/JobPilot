"""Short opaque identifiers safe for Telegram's 64-byte callback_data limit."""

import secrets


def short_id() -> str:
    """Return a random 11-character URL-safe id (64 random bits)."""
    return secrets.token_urlsafe(8)
