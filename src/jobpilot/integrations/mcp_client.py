"""Connections to the self-hosted MCP servers, each run as a subprocess over stdio.

The worker opens read-only connections, the dispatcher send-capable ones. The caller name and its
per-connection key travel in the subprocess environment; the server verifies both and applies the
same scope file. Starting the subprocess (entering the context) is done by apps/app only.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Protocol

from mcp import Client, StdioServerParameters
from opentelemetry import trace

from jobpilot.core.config import REPO_ROOT
from jobpilot.core.errors import DeliveryError
from jobpilot.integrations import mcp_scopes
from jobpilot.safety.mcp_keys import caller_key

SERVER_MODULES = {
    "telegram": "jobpilot.servers.telegram.server",
    "gmail": "jobpilot.servers.gmail.server",
    "web": "jobpilot.servers.web.server",
}
# Settings each server needs. The MCP SDK starts subprocesses with a minimal environment, so these are
# passed through explicitly when set in this process (otherwise the server reads them from .env itself).
SERVER_ENV = {
    "telegram": [
        "JOBPILOT_ENV",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_STORAGE_BUCKET",
        "MCP_TELEGRAM_DISPATCHER_KEY",
        "MCP_TELEGRAM_WORKER_KEY",
    ],
    "gmail": [
        "JOBPILOT_ENV",
        "GMAIL_FIXTURE_DIR",
        "GMAIL_OAUTH_CLIENT_ID",
        "GMAIL_OAUTH_CLIENT_SECRET",
        "GMAIL_TOKEN_KEY",
        "APPROVAL_TOKEN_KEY",
        "APPROVAL_VERIFY_KEY",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_STORAGE_BUCKET",
        "MCP_GMAIL_DISPATCHER_KEY",
        "MCP_GMAIL_WORKER_KEY",
    ],
    "web": ["JOBPILOT_ENV", "OPENAI_API_KEY", "MCP_WEB_WORKER_KEY"],
}
tracer = trace.get_tracer("jobpilot.mcp")


def server_params(server: str, caller: str, extra_env: dict[str, str] | None = None) -> StdioServerParameters:
    """Build the subprocess command and environment for one MCP server.

    Args:
        server: ``telegram``, ``gmail`` or ``web``.
        caller: ``worker`` or ``dispatcher``; decides the scopes.
        extra_env: Extra environment variables for the subprocess.

    Returns:
        The stdio parameters to start the server with.
    """
    names = set(SERVER_ENV[server]) | {"TELEGRAM_CHAT_ID", "DATABASE_URL", "VERCEL"}
    env = {name: os.environ[name] for name in names if name in os.environ}
    # The server checks this caller name and key before every call.
    env.update({"MCP_CALLER": caller, "MCP_CALLER_KEY": caller_key(server, caller), **(extra_env or {})})
    return StdioServerParameters(
        command=sys.executable, args=["-m", SERVER_MODULES[server]], env=env, cwd=str(REPO_ROOT)
    )


class ToolCaller(Protocol):
    """Anything that can call an MCP tool (a real client or a test fake)."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call tool ``name`` with ``arguments`` and return the raw result."""


class MCPConnection:
    """A typed wrapper around one MCP client session: checks scope before every call."""

    def __init__(self, server: str, caller: str, client: ToolCaller) -> None:
        """Wrap ``client`` as ``caller``'s connection to ``server``."""
        self.server = server
        self.caller = caller
        self.client = client

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one tool after the app-side scope check.

        Args:
            tool: The tool name.
            arguments: The tool's arguments.

        Returns:
            The tool's structured result.

        Raises:
            ScopeDenied: The caller may not use this tool.
            DeliveryError: The tool returned an error.
        """
        mcp_scopes.check(self.caller, self.server, tool)
        with tracer.start_as_current_span(
            "mcp.call", attributes={"mcp.server": self.server, "mcp.tool": tool, "mcp.caller": self.caller}
        ):
            result = await self.client.call_tool(tool, arguments)
        if result.is_error:
            text = " ".join(getattr(c, "text", "") for c in result.content)
            raise DeliveryError(f"{self.server}.{tool} error: {text}", retryable=False)
        data = result.structured_content or {}
        return dict(data)


def open_client(server: str, caller: str, extra_env: dict[str, str] | None = None) -> Client:
    """An unopened MCP client; `async with` it to start the subprocess."""
    return Client(server_params(server, caller, extra_env), read_timeout_seconds=60)
