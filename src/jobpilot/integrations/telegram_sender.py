"""Typed wrapper for the Telegram MCP server, used as the dispatcher's 'telegram' sender."""

from __future__ import annotations

from typing import Any

from jobpilot.core.errors import DeliveryError, DeliverySkipped
from jobpilot.integrations.mcp_client import MCPConnection

OPS = {"send_message", "edit_message", "send_document", "send_photo"}
# Telegram answers that can never succeed on retry and must not freeze the partition behind them.
TERMINAL_EDIT_ERRORS = (
    "message to edit not found",
    "message can't be edited",
    "message_id_invalid",
    "message is too old",
)


class TelegramSender:
    """Delivers Telegram outbox rows through the MCP server."""

    def __init__(self, connection: MCPConnection) -> None:
        """Wrap a send-capable Telegram MCP connection (dispatcher only)."""
        self.connection = connection

    async def deliver(self, payload: dict[str, Any]) -> str | None:
        """Run one Telegram outbox operation.

        Args:
            payload: The outbox payload; ``op`` picks the tool.

        Returns:
            The Telegram message id, if the tool returned one.

        Raises:
            DeliverySkipped: The edit target is gone, or a preview image
                is
                missing; retrying can't help, so the row is skipped.
            DeliveryError: Any other failure (retryable if Telegram says
                so).
        """
        op = payload.get("op")
        if op not in OPS:
            raise DeliveryError(f"unknown telegram op {op!r}", retryable=False)
        args = {k: v for k, v in payload.items() if k not in {"op", "queued_at"}}
        result = await self.connection.call(op, args)
        if not result.get("ok"):
            error = str(result.get("error") or "telegram error")
            if op == "edit_message" and any(t in error.lower() for t in TERMINAL_EDIT_ERRORS):
                raise DeliverySkipped(f"edit target is gone: {error}")
            if op == "send_photo" and "preview not found" in error:
                raise DeliverySkipped("no preview image for this resume; the PDF follows")
            raise DeliveryError(
                error,
                retryable=bool(result.get("retryable")),
                retry_after=result.get("retry_after"),
            )
        message_id = result.get("message_id")
        return str(message_id) if message_id is not None else None
