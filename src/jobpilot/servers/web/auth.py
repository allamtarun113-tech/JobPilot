"""Caller authentication and scope checks for the read-only web MCP server."""

from __future__ import annotations

import hmac
import os

from jobpilot.integrations.mcp_scopes import allowed
from jobpilot.safety.mcp_keys import caller_key


class Refused(Exception):
    """The server refuses the call (bad caller or scope)."""

    pass


def check_scope(tool: str) -> None:
    """Raise Refused unless the worker, with its key, may call ``web.<tool>``."""
    caller = os.environ.get("MCP_CALLER", "")
    if caller != "worker":
        raise Refused(f"unknown caller {caller!r}")
    if not hmac.compare_digest(os.environ.get("MCP_CALLER_KEY", ""), caller_key("web", caller)):
        raise Refused(f"bad key for caller {caller!r}")
    if tool not in allowed(caller, "web"):
        raise Refused(f"caller {caller!r} may not call web.{tool}")
