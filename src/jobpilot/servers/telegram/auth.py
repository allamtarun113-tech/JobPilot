"""Caller authentication, scope checks and the chat-id lock for the Telegram MCP server.

The server runs as a subprocess of the app under the same OS user, so these checks guard against
bugs (a feature calling send by mistake), not a compromised app.
"""

from __future__ import annotations

import hmac
import os

from jobpilot.core.config import get_settings
from jobpilot.integrations.mcp_scopes import allowed
from jobpilot.safety.mcp_keys import caller_key


class Refused(Exception):
    """The server refuses the call (bad caller, scope, or chat)."""

    pass


def authenticated_caller() -> str:
    """Verify MCP_CALLER / MCP_CALLER_KEY from the environment the parent process gave us."""
    caller = os.environ.get("MCP_CALLER", "")
    presented = os.environ.get("MCP_CALLER_KEY", "")
    if caller not in ("worker", "dispatcher"):
        raise Refused(f"unknown caller {caller!r}")
    if not hmac.compare_digest(presented, caller_key("telegram", caller)):
        raise Refused(f"bad key for caller {caller!r}")
    return caller


def check_scope(tool: str) -> None:
    """Raise Refused unless the verified caller may call ``telegram.<tool>``."""
    caller = authenticated_caller()
    if tool not in allowed(caller, "telegram"):
        raise Refused(f"caller {caller!r} may not call telegram.{tool}")


def locked_chat_id(requested: int | None) -> int:
    """Only the owner's chat is ever addressed."""
    owner = get_settings().telegram_chat_id
    if owner is None:
        if get_settings().telegram_dry_run and requested is None:
            return 0  # dry-run before setup: nothing is really sent
        raise Refused("TELEGRAM_CHAT_ID is not set")
    if requested is not None and requested != owner:
        raise Refused("chat id is not the owner's chat")
    return owner
