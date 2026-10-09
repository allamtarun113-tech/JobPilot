"""Gmail sender: exactly-once retries and the Sent lookup."""

import pytest

from jobpilot.core.errors import DeliveryError
from jobpilot.integrations.gmail_sender import GmailSender


class CrashAfterAcceptConnection:
    """A Gmail connection that drops right after Gmail accepted the send."""

    def __init__(self) -> None:
        """Start with nothing sent."""
        self.sent_id: str | None = None
        self.send_calls = 0

    async def call(self, tool: str, arguments: dict) -> dict:
        """Simulate find_sent and a send that fails after succeeding."""
        if tool == "find_sent":
            return {
                "ok": True,
                "message": {"message_id": self.sent_id} if self.sent_id else None,
            }
        if tool == "send_draft":
            self.send_calls += 1
            self.sent_id = "gmail-accepted-1"
            raise DeliveryError("connection dropped after Gmail accepted the message")
        raise AssertionError(tool)


async def test_retry_after_crash_finds_sent_and_does_not_send_twice() -> None:
    """A retry after a lost reply finds the sent email instead of resending."""
    connection = CrashAfterAcceptConnection()
    sender = GmailSender(connection)  # type: ignore[arg-type]
    payload = {
        "op": "send_draft",
        "job_id": 9,
        "draft_id": "draft-9",
        "version": 1,
        "approval_token": "short-lived-token",
        "email_id": 4,
        "approval_id": 3,
    }
    with pytest.raises(DeliveryError):
        await sender.deliver(payload)
    assert await sender.deliver(payload) == "gmail-accepted-1"
    assert connection.send_calls == 1


async def test_find_sent_uses_the_draft_thread_and_job_header(monkeypatch) -> None:
    """Verified on real Gmail: the sent draft stays in its thread with X-JobPilot-Job."""
    import httpx

    from jobpilot.servers.gmail.tools import GmailApi

    thread = {
        "messages": [
            {
                "id": "draft-msg",
                "labelIds": ["DRAFT"],
                "payload": {"headers": [{"name": "X-JobPilot-Job", "value": "7"}]},
            },
            {
                "id": "other",
                "labelIds": ["SENT"],
                "payload": {"headers": [{"name": "X-JobPilot-Job", "value": "8"}]},
            },
            {
                "id": "sent-msg",
                "labelIds": ["SENT"],
                "payload": {"headers": [{"name": "X-JobPilot-Job", "value": "7"}]},
            },
        ]
    }

    async def fake_get(self, path, params=None):  # type: ignore[no-untyped-def]
        """Return a thread with a draft and two sent messages."""
        assert path == "/threads/t1"
        return httpx.Response(200, json=thread, request=httpx.Request("GET", "https://x"))

    monkeypatch.setattr(GmailApi, "_get", fake_get)
    gmail = GmailApi("id", "secret", "refresh")
    assert await gmail.find_sent(7, "t1") == "sent-msg"
    assert await gmail.find_sent(9, "t1") is None
    assert await gmail.find_sent(7, None) is None
