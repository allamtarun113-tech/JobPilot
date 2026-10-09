"""Spike: how can a retry tell that an API-sent draft really went out?

Sends ONE short test email from your Gmail **to yourself** (nobody else), then reports each candidate signal:
the deterministic Message-ID in Sent, the draft's own message id, the X-JobPilot-Job header, and whether the
draft disappeared. The exactly-once email retry depends on these. Run after `make gmail-auth`:
  make gmail-spike
"""

from __future__ import annotations

import asyncio
import base64
import random

from jobpilot.core.config import get_settings
from jobpilot.servers.gmail import auth
from jobpilot.servers.gmail.tools import GmailApi, build_message, message_id_for


async def main() -> None:
    """Send one test email to yourself and report which retry signals survive."""
    s = get_settings()
    token = auth.load_token()
    if not token or not s.gmail_oauth_client_id or not s.gmail_oauth_client_secret:
        raise SystemExit("Gmail is not connected - run `make gmail-auth` first")
    secret = s.gmail_oauth_client_secret.get_secret_value()
    gmail = GmailApi(s.gmail_oauth_client_id, secret, token["refresh_token"])
    me = (await gmail._get("/profile")).json()["emailAddress"]
    job_id = 990_000_000 + random.randint(0, 9_999_999)  # never collides with a real job id
    message = build_message(
        me, "JobPilot Sent-lookup test", "Test from JobPilot - you can delete it.", job_id
    )
    raw = base64.urlsafe_b64encode(message).decode()
    created = (await gmail._post("/drafts", {"message": {"raw": raw}})).json()
    draft_id, thread_id = created["id"], created["message"]["threadId"]
    sent_id = await gmail.send_draft(draft_id)
    print(f"sent a test email to yourself ({me})")
    await asyncio.sleep(10)
    sent = (
        await gmail._get(
            f"/messages/{sent_id}",
            {"format": "metadata", "metadataHeaders": ["Message-ID", "X-JobPilot-Job"]},
        )
    ).json()
    headers = {h["name"].lower(): h["value"] for h in sent.get("payload", {}).get("headers", [])}
    thread = (await gmail._get(f"/threads/{thread_id}", {"format": "minimal"})).json()
    in_thread_sent = [m["id"] for m in thread.get("messages", []) if "SENT" in (m.get("labelIds") or [])]
    print(f"1. sent message stays in the draft's thread: {sent.get('threadId') == thread_id}")
    print(f"   that thread holds a SENT message: {sent_id in in_thread_sent}")
    print(
        f"2. X-JobPilot-Job header kept on the sent message: {headers.get('x-jobpilot-job') == str(job_id)}"
    )
    print(f"3. custom Message-ID kept: {headers.get('message-id') == message_id_for(job_id)}")
    print(f"4. draft deleted after send: {not await gmail.draft_exists(draft_id)}")


if __name__ == "__main__":
    asyncio.run(main())
