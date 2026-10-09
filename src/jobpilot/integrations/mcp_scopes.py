"""App-side MCP scope check (config/mcp_scopes.yaml). Each server checks again on its side."""

from __future__ import annotations

from jobpilot.core.config import get_config
from jobpilot.core.errors import ScopeDenied


def allowed(caller: str, server: str) -> list[str]:
    """Return the tools ``caller`` may call on ``server``."""
    return list(get_config().mcp_scopes.get(caller, {}).get(server, []))


def check(caller: str, server: str, tool: str) -> None:
    """Raise ScopeDenied unless ``caller`` may call ``server.tool``."""
    if tool not in allowed(caller, server):
        raise ScopeDenied(f"{caller} may not call {server}.{tool}")
