"""The Gmail MCP server (fixture mode): token checks and scopes."""

from pathlib import Path

from jobpilot.integrations.mcp_client import open_client
from jobpilot.safety.approval_tokens import mint


async def test_gmail_refuses_send_without_valid_token_and_reconciles_sent(tmp_path: Path) -> None:
    """Sends without a valid token are refused; a sent email is found again."""
    env = {"GMAIL_FIXTURE_DIR": str(tmp_path), "APPROVAL_TOKEN_KEY": "test-approval-key"}
    async with open_client("gmail", "dispatcher", extra_env=env) as client:
        created = (
            await client.call_tool(
                "create_draft",
                {"to": "careers@example.com", "subject": "Application", "body": "Hello", "job_id": 7},
            )
        ).structured_content
        draft_id = created["message"]["draft_id"]
        tokenless = await client.call_tool("send_draft", {"job_id": 7, "draft_id": draft_id, "version": 1})
        assert tokenless.is_error
        refused = (
            await client.call_tool(
                "send_draft",
                {"job_id": 7, "draft_id": draft_id, "version": 1, "approval_token": "bad"},
            )
        ).structured_content
        assert not refused["ok"] and "refused" in refused["error"]

        token = mint(b"test-approval-key", job_id=7, draft_id=draft_id, version=1)
        sent = (
            await client.call_tool(
                "send_draft",
                {"job_id": 7, "draft_id": draft_id, "version": 1, "approval_token": token},
            )
        ).structured_content
        found = (await client.call_tool("find_sent", {"job_id": 7})).structured_content
        assert sent["message"]["message_id"] == found["message"]["message_id"]


async def test_worker_cannot_create_or_send_drafts(tmp_path: Path) -> None:
    """The worker's connection may not create Gmail drafts."""
    async with open_client("gmail", "worker", extra_env={"GMAIL_FIXTURE_DIR": str(tmp_path)}) as client:
        result = (
            await client.call_tool(
                "create_draft", {"to": "x@example.com", "subject": "x", "body": "x", "job_id": 1}
            )
        ).structured_content
        assert not result["ok"] and "may not call" in result["error"]
