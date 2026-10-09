"""Typed Gmail MCP sender used only by the dispatcher."""

from __future__ import annotations

from typing import Any

from jobpilot.core.errors import DeliveryError
from jobpilot.integrations.mcp_client import MCPConnection

OPS = {"create_draft", "send_draft"}


class GmailSender:
    """Delivers Gmail outbox rows (create_draft, send_draft) through the MCP server."""

    def __init__(self, connection: MCPConnection) -> None:
        """Wrap a send-capable Gmail MCP connection (dispatcher only)."""
        self.connection = connection

    async def reconcile(self, payload: dict[str, Any]) -> str | None:
        """Check whether this job's email was already sent, before any send.

        Two signals: a SENT message in the draft's thread carrying
        X-JobPilot-Job, and the stored draft - Gmail deletes a draft
        when it
        is sent. A missing draft with no sent message found is never
        resent
        blindly.

        Args:
            payload: The send_draft outbox payload.

        Returns:
            The Gmail message id if the email was already sent, else
                None.

        Raises:
            DeliveryError: The search failed, or the draft vanished
                without
                a sent message (needs a person to reconcile).
        """
        found = await self.connection.call(
            "find_sent",
            {
                "job_id": payload["job_id"],
                "draft_id": payload.get("draft_id"),
                "thread_id": payload.get("thread_id"),
            },
        )
        if not found.get("ok"):
            raise DeliveryError(found.get("error") or "Gmail sent search failed")
        message = found.get("message") or {}
        if message.get("message_id"):
            return str(message["message_id"])
        if message.get("draft_exists") is False:
            raise DeliveryError(
                "the Gmail draft no longer exists but no sent message was found yet - it may have been sent "
                "(search can lag) or deleted; reconcile before any resend",
                retryable=False,
            )
        return None

    async def deliver(self, payload: dict[str, Any]) -> str | None:
        """Run one Gmail outbox operation.

        Args:
            payload: The outbox payload; ``op`` picks the tool.

        Returns:
            The draft id (with ``|thread_id`` when known) or the message
                id.

        Raises:
            DeliveryError: Unknown op, or the tool failed.
        """
        op = payload.get("op")
        if op not in OPS:
            raise DeliveryError(f"unknown gmail op {op!r}", retryable=False)
        # Bookkeeping fields stay in the outbox; the tool gets the rest.
        args = {
            k: v
            for k, v in payload.items()
            if k not in {"op", "email_id", "approval_id", "queued_at", "thread_id"}
        }
        # Retry after a crash: if the email already went out, don't
        # resend.
        if op == "send_draft" and (already := await self.reconcile(payload)):
            return already
        result = await self.connection.call(op, args)
        if not result.get("ok"):
            raise DeliveryError(result.get("error") or f"Gmail {op} failed")
        message = result.get("message") or {}
        external_id = message.get("draft_id" if op == "create_draft" else "message_id")
        if not external_id:
            raise DeliveryError(f"Gmail {op} returned no id")
        if op == "create_draft" and message.get("thread_id"):
            return f"{external_id}|{message['thread_id']}"  # the dispatcher stores both
        return str(external_id)
