"""The real Telegram MCP server as a stdio subprocess, in dry-run mode."""

import pytest

from jobpilot.core.errors import ScopeDenied
from jobpilot.integrations.mcp_client import MCPConnection, open_client
from jobpilot.integrations.telegram_sender import TelegramSender


async def test_dispatcher_can_send_to_owner_only() -> None:
    """The dispatcher can message the owner's chat and no other."""
    async with open_client("telegram", "dispatcher") as c:
        ok = (await c.call_tool("send_message", {"text": "hello"})).structured_content
        assert ok["ok"] and ok["dry_run"] and ok["message_id"]
        other = (await c.call_tool("send_message", {"text": "x", "chat_id": 999})).structured_content
        assert not other["ok"] and "owner" in other["error"]


async def test_worker_connection_cannot_send() -> None:
    """The worker can't send, checked both in the app and in the server."""
    async with open_client("telegram", "worker") as c:
        # app-side check
        with pytest.raises(ScopeDenied):
            await MCPConnection("telegram", "worker", c).call("send_message", {"text": "x"})
        # server-side check, bypassing the app wrapper
        result = (await c.call_tool("send_message", {"text": "x"})).structured_content
        assert not result["ok"] and "may not call" in result["error"]


async def test_wrong_caller_key_is_refused() -> None:
    """A wrong caller key is refused."""
    async with open_client("telegram", "dispatcher", extra_env={"MCP_CALLER_KEY": "wrong"}) as c:
        result = (await c.call_tool("send_message", {"text": "x"})).structured_content
        assert not result["ok"] and "bad key" in result["error"]


async def test_sender_returns_message_id() -> None:
    """Sends return a message id that edits can target."""
    async with open_client("telegram", "dispatcher") as c:
        sender = TelegramSender(MCPConnection("telegram", "dispatcher", c))
        mid = await sender.deliver({"op": "send_message", "text": "hi"})
        assert mid is not None
        assert await sender.deliver({"op": "edit_message", "message_id": int(mid), "text": "hi2"}) == mid


async def test_cards_may_use_html_but_no_other_parse_mode() -> None:
    """HTML formatting is allowed; other parse modes are refused."""
    async with open_client("telegram", "dispatcher") as c:
        ok = (
            await c.call_tool("send_message", {"text": "<b>Card</b>", "parse_mode": "HTML"})
        ).structured_content
        assert ok["ok"]
        bad = (
            await c.call_tool("send_message", {"text": "*x*", "parse_mode": "MarkdownV2"})
        ).structured_content
        assert not bad["ok"] and "parse_mode" in bad["error"]


async def test_send_photo_sends_only_stored_resume_previews() -> None:
    """Only stored resume previews can be sent; a missing one is skipped."""
    from jobpilot.core.errors import DeliverySkipped

    async with open_client("telegram", "dispatcher") as c:
        other = (await c.call_tool("send_photo", {"storage_key": "resumes/jobs/1/x.pdf"})).structured_content
        assert not other["ok"] and "only stored resume previews" in other["error"]
        outside = (await c.call_tool("send_photo", {"storage_key": "secrets/key.png"})).structured_content
        assert not outside["ok"]
        sender = TelegramSender(MCPConnection("telegram", "dispatcher", c))
        with pytest.raises(DeliverySkipped):  # a version from before previews: skipped, the PDF still follows
            await sender.deliver({"op": "send_photo", "storage_key": "resumes/jobs/1/missing.png"})
