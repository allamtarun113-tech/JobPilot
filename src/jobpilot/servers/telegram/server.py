"""Self-hosted Telegram MCP server (stdio subprocess).

Exposes only: send_message, send_document, send_photo, edit_message.
No delete, no forwarding, no sending to any chat but the owner's.
Incoming messages are NOT read here: the worker reads them by long polling.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from mcp.server import MCPServer
from pydantic import BaseModel

from jobpilot.integrations.storage import NotFound, get_store
from jobpilot.servers.telegram import auth, tools

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
server = MCPServer("jobpilot-telegram")


class ToolResult(BaseModel):
    """What every Telegram tool returns, including retry hints."""

    ok: bool
    message_id: int | None = None
    error: str | None = None
    retryable: bool = False
    retry_after: float | None = None
    dry_run: bool = False


def _refused(exc: Exception) -> ToolResult:
    """Return a non-retryable refusal result."""
    return ToolResult(ok=False, error=f"refused: {exc}", retryable=False)


async def _call(tool: str, method: str, chat_id: int | None, params: dict[str, Any]) -> ToolResult:
    """Check scope and chat, then call one Bot API method.

    Args:
        tool: The MCP tool name (for the scope check).
        method: The Bot API method, e.g. ``sendMessage``.
        chat_id: The requested chat; only the owner's is allowed.
        params: The method's parameters; None values are dropped.

    Returns:
        The call's result.
    """
    try:
        auth.check_scope(tool)
        params = {"chat_id": auth.locked_chat_id(chat_id), **params}
    except auth.Refused as exc:
        return _refused(exc)
    return ToolResult(**await tools.call(method, {k: v for k, v in params.items() if v is not None}))


# Cards carry learning and apply links; Telegram would unfold the first one into a video or page preview.
NO_PREVIEW = {"is_disabled": True}
PARSE_MODES = {"HTML"}  # cards are formatted with Telegram's HTML subset (decision 32)


def _parse_mode(value: str | None) -> str | None:
    """Return the parse mode if allowed (only Telegram HTML), else refuse."""
    if value is not None and value not in PARSE_MODES:
        raise auth.Refused(f"parse_mode {value!r} is not allowed")
    return value


@server.tool()
async def send_message(
    text: str,
    reply_markup: dict[str, Any] | None = None,
    chat_id: int | None = None,
    parse_mode: str | None = None,
) -> ToolResult:
    """Send a text message to the owner's chat (optionally formatted with Telegram HTML)."""
    try:
        mode = _parse_mode(parse_mode)
    except auth.Refused as exc:
        return _refused(exc)
    return await _call(
        "send_message",
        "sendMessage",
        chat_id,
        {
            "text": text[:4096],
            "reply_markup": reply_markup,
            "link_preview_options": NO_PREVIEW,
            "parse_mode": mode,
        },
    )


@server.tool()
async def edit_message(
    message_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
    chat_id: int | None = None,
    parse_mode: str | None = None,
) -> ToolResult:
    """Edit a message previously sent to the owner's chat."""
    try:
        mode = _parse_mode(parse_mode)
    except auth.Refused as exc:
        return _refused(exc)
    return await _call(
        "edit_message",
        "editMessageText",
        chat_id,
        {
            "message_id": message_id,
            "text": text[:4096],
            "reply_markup": reply_markup,
            "link_preview_options": NO_PREVIEW,
            "parse_mode": mode,
        },
    )


@server.tool()
async def send_photo(storage_key: str, caption: str | None = None, chat_id: int | None = None) -> ToolResult:
    """Send a stored resume preview image (page 1 as PNG) to the owner's chat."""
    try:
        auth.check_scope("send_photo")
        locked = auth.locked_chat_id(chat_id)
        if not storage_key.startswith("resumes/") or not storage_key.endswith(".png"):
            raise auth.Refused("only stored resume previews may be sent")
        data = await get_store().get(storage_key)
    except NotFound:
        return ToolResult(ok=False, error="preview not found", retryable=False)
    except (auth.Refused, ValueError) as exc:
        return _refused(exc)
    return ToolResult(
        **await tools.call_upload(
            "sendPhoto", "photo", locked, data, storage_key.rsplit("/", 1)[-1], "image/png",
            caption[:1024] if caption else None,
        )
    )  # fmt: skip


@server.tool()
async def send_document(
    document_url: str | None = None,
    storage_key: str | None = None,
    caption: str | None = None,
    reply_markup: dict[str, Any] | None = None,
    chat_id: int | None = None,
) -> ToolResult:
    """Send a document (e.g. a resume PDF) by URL to the owner's chat."""
    if storage_key:
        try:
            auth.check_scope("send_document")
            locked = auth.locked_chat_id(chat_id)
            if not storage_key.startswith("resumes/") or not storage_key.endswith(".pdf"):
                raise auth.Refused("only stored resume PDFs may be sent")
            data = await get_store().get(storage_key)
            return ToolResult(
                **await tools.call_document(
                    locked,
                    data,
                    storage_key.rsplit("/", 1)[-1],
                    caption[:1024] if caption else None,
                    reply_markup,
                )
            )
        except (auth.Refused, NotFound, ValueError) as exc:
            return _refused(exc)
    if not document_url:
        return _refused(ValueError("document_url or storage_key is required"))
    return await _call(
        "send_document",
        "sendDocument",
        chat_id,
        {
            "document": document_url,
            "caption": caption[:1024] if caption else None,
            "reply_markup": reply_markup,
        },
    )


if __name__ == "__main__":
    server.run("stdio")
