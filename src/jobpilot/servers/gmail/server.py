"""Self-hosted Gmail MCP server (stdio subprocess), used only by the dispatcher.

Tools: create_draft, find_sent and send_draft (send_draft checks an approval-minted token). No reading of
your inbox, no delete, no forwarding, no label changes. GMAIL_FIXTURE_DIR switches to a fake in-memory Gmail.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import httpx
from mcp.server import MCPServer
from pydantic import BaseModel

from jobpilot.core.config import get_settings
from jobpilot.core.errors import InvalidApprovalToken
from jobpilot.integrations.storage import NotFound, get_store
from jobpilot.servers.gmail import auth, token_verify
from jobpilot.servers.gmail.tools import GmailApi, build_message

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
server = MCPServer("jobpilot-gmail")
# The real Gmail client, created on first use.
_mailbox: GmailApi | None = None
# Fixture mode (tests): an in-memory stand-in for Gmail.
_fixture_drafts: dict[str, dict[str, Any]] = {}
_fixture_sent: dict[int, str] = {}


def mailbox() -> GmailApi:
    """Return the Gmail API client, signing in from the stored token once.

    Raises:
        auth.Refused: Gmail is not connected yet.
    """
    global _mailbox
    if _mailbox is None:
        s = get_settings()
        token = auth.load_token()
        if not token or not s.gmail_oauth_client_id or not s.gmail_oauth_client_secret:
            raise auth.Refused("Gmail is not connected - run `make gmail-auth`")
        _mailbox = GmailApi(
            s.gmail_oauth_client_id,
            s.gmail_oauth_client_secret.get_secret_value(),
            token["refresh_token"],
        )
    return _mailbox


class MessageResult(BaseModel):
    """What every Gmail tool returns: ok, a message dict, or an error."""

    ok: bool
    message: dict[str, Any] | None = None
    error: str | None = None


async def _attachment(key: str | None) -> tuple[str, bytes] | None:
    """The approved resume PDF, read from the private store. Only stored resume PDFs may be attached."""
    if not key:
        return None
    if not key.startswith("resumes/") or not key.endswith(".pdf"):
        raise auth.Refused("only stored resume PDFs may be attached")
    return "resume.pdf", await get_store().get(key)


@server.tool()
async def create_draft(
    to: str, subject: str, body: str, job_id: int, attachment_key: str | None = None
) -> MessageResult:
    """Create a Gmail draft (never sends). The approved resume PDF is attached when given."""
    try:
        auth.check_scope("create_draft")
        attachment = await _attachment(attachment_key)
        if os.environ.get("GMAIL_FIXTURE_DIR"):
            draft_id = f"draft-{job_id}-{len(_fixture_drafts) + 1}"
            thread_id = f"thread-{job_id}"
            _fixture_drafts[draft_id] = {
                "to": to,
                "subject": subject,
                "body": body,
                "job_id": job_id,
                "attachment": attachment[0] if attachment else None,
                "raw_bytes": len(build_message(to, subject, body, job_id, attachment)),
            }
        else:
            draft_id, thread_id = await mailbox().create_draft(to, subject, body, job_id, attachment)
        return MessageResult(
            ok=True,
            message={"draft_id": draft_id, "thread_id": thread_id, "attached": attachment is not None},
        )
    except (auth.Refused, NotFound, RuntimeError, ValueError, httpx.HTTPError) as exc:
        return MessageResult(ok=False, error=f"refused: {exc}")


@server.tool()
async def find_sent(job_id: int, draft_id: str | None = None, thread_id: str | None = None) -> MessageResult:
    """Look for the job's email: a SENT message in the draft's thread carrying X-JobPilot-Job; also report
    whether the draft still exists - Gmail deletes a draft when it is sent."""
    try:
        auth.check_scope("find_sent")
        if os.environ.get("GMAIL_FIXTURE_DIR"):
            message_id = _fixture_sent.get(job_id)
            exists = draft_id in _fixture_drafts if draft_id else None
        else:
            box = mailbox()
            message_id = await box.find_sent(job_id, thread_id)
            exists = await box.draft_exists(draft_id) if draft_id else None
        return MessageResult(ok=True, message={"message_id": message_id, "draft_exists": exists})
    except (auth.Refused, RuntimeError, httpx.HTTPError) as exc:
        return MessageResult(ok=False, error=f"refused: {exc}")


@server.tool()
async def send_draft(job_id: int, draft_id: str, version: int, approval_token: str) -> MessageResult:
    """Send a draft after verifying the approval token for it.

    Args:
        job_id: The job the email is for.
        draft_id: The Gmail draft to send.
        version: The approved email version.
        approval_token: The short-lived token minted by the dispatcher.

    Returns:
        The sent message id, or the reason it was refused.
    """
    try:
        auth.check_scope("send_draft")
        # The token must name exactly this job, draft and version.
        token_verify.check(approval_token, job_id, draft_id, version)
        if os.environ.get("GMAIL_FIXTURE_DIR"):
            if draft_id not in _fixture_drafts:
                raise ValueError("unknown draft")
            message_id = _fixture_sent.setdefault(job_id, f"sent-{job_id}")
            _fixture_drafts.pop(draft_id, None)  # Gmail deletes a draft once it is sent
        else:
            message_id = await mailbox().send_draft(draft_id)
        return MessageResult(ok=True, message={"message_id": message_id})
    except (auth.Refused, InvalidApprovalToken, RuntimeError, ValueError, httpx.HTTPError) as exc:
        return MessageResult(ok=False, error=f"refused: {exc}")


if __name__ == "__main__":
    server.run("stdio")
