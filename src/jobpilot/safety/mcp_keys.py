"""Per-connection MCP caller keys, shared by the app-side clients and the MCP server subprocesses."""

from __future__ import annotations

from jobpilot.core.config import get_settings

CALLER_KEY_SETTING = {
    ("telegram", "dispatcher"): "mcp_telegram_dispatcher_key",
    ("telegram", "worker"): "mcp_telegram_worker_key",
    ("gmail", "dispatcher"): "mcp_gmail_dispatcher_key",
    ("gmail", "worker"): "mcp_gmail_worker_key",
    ("web", "worker"): "mcp_web_worker_key",
}
DEV_KEYS = {  # used only when the .env key is empty, so the skeleton runs before setup
    ("telegram", "dispatcher"): "dev-telegram-dispatcher-key",
    ("telegram", "worker"): "dev-telegram-worker-key",
    ("gmail", "dispatcher"): "dev-gmail-dispatcher-key",
    ("gmail", "worker"): "dev-gmail-worker-key",
    ("web", "worker"): "dev-web-worker-key",
}


def caller_key(server: str, caller: str) -> str:
    """Return the key a caller presents to an MCP server.

    Args:
        server: ``telegram``, ``gmail`` or ``web``.
        caller: ``dispatcher`` or ``worker``.

    Returns:
        The key from ``.env``, or a fixed dev key when it isn't set.
    """
    secret = getattr(get_settings(), CALLER_KEY_SETTING[(server, caller)])
    return secret.get_secret_value() if secret and secret.get_secret_value() else DEV_KEYS[(server, caller)]
