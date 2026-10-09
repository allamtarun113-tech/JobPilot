"""MCP server subprocesses that come back after a crash.

Only apps/app starts subprocesses. If a server process (or its stdio pipe) dies, the next call reopens it.
The call that hit the dead connection is NOT re-issued - it may have reached the server (a send) - so it is
reported as a retryable DeliveryError and the normal retry path (with Sent reconciliation for email) runs.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any

import anyio

from jobpilot.core.errors import DeliveryError
from jobpilot.integrations.mcp_client import open_client

log = logging.getLogger(__name__)
CONNECTION_ERRORS: tuple[type[BaseException], ...] = (
    anyio.ClosedResourceError,
    anyio.BrokenResourceError,
    anyio.EndOfStream,
    BrokenPipeError,
    ConnectionError,
    EOFError,
)


class ManagedClient:
    """An MCP client that reopens its server subprocess after a crash."""

    def __init__(self, server: str, caller: str) -> None:
        """Prepare (but don't start) ``caller``'s client for ``server``."""
        self.server, self.caller = server, caller
        self._stack: AsyncExitStack | None = None
        self._client: Any = None

    async def open(self) -> None:
        """Start the server subprocess and its client session."""
        stack = AsyncExitStack()
        self._client = await stack.enter_async_context(open_client(self.server, self.caller))
        self._stack = stack

    async def close(self) -> None:
        """Stop the client session and the subprocess, ignoring close errors."""
        if self._stack is not None:
            # Clear first, so a failing close is never retried.
            stack, self._stack = self._stack, None
            try:
                await stack.aclose()
            except Exception:  # a dead subprocess can fail to close cleanly
                log.debug("closing MCP %s failed", self.server, exc_info=True)

    async def __aenter__(self) -> ManagedClient:
        """Open the client for an ``async with`` block."""
        await self.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Close the client when the block ends."""
        await self.close()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool, reopening the server if the connection died.

        The failed call is not repeated: it may have reached the server.

        Raises:
            DeliveryError: The connection was lost during this call
                (retryable through the normal outbox path).
        """
        if self._client is None:
            await self.open()
        try:
            return await self._client.call_tool(name, arguments)
        except CONNECTION_ERRORS as exc:
            log.warning("MCP %s connection lost during %s; reopening", self.server, name)
            await self.close()
            self._client = None
            try:
                await self.open()
            except Exception:
                log.exception("reopening MCP %s failed", self.server)
            raise DeliveryError(f"{self.server}.{name}: connection lost ({type(exc).__name__})") from exc
