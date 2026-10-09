"""Inbound chat-id check: only the owner's chat is listened to."""

from __future__ import annotations

import logging

from jobpilot.core.config import get_settings

log = logging.getLogger(__name__)


def is_owner(chat_id: int | None) -> bool:
    """Return True only for the owner's chat id (TELEGRAM_CHAT_ID).

    With no owner configured every chat is ignored, and the id is
    logged so it can be copied into ``.env``.
    """
    owner = get_settings().telegram_chat_id
    if owner is None:
        log.warning(
            "TELEGRAM_CHAT_ID is not set; ignoring message from chat %s (put this id in .env)", chat_id
        )
        return False
    return chat_id == owner
