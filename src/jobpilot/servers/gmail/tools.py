"""Gmail REST API access behind the MCP tools.

Uses an OAuth access token refreshed from the stored refresh token.
"""

from __future__ import annotations

import asyncio
import base64
import time
from email.message import EmailMessage
from typing import Any

import httpx

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def message_id_for(job_id: int) -> str:
    """The deterministic Message-ID searched in Sent before any retry."""
    return f"<jobpilot-job-{job_id}@jobpilot.local>"


def build_message(
    to: str, subject: str, body: str, job_id: int, attachment: tuple[str, bytes] | None = None
) -> bytes:
    """Build the raw MIME email for a draft.

    Args:
        to: The recipient.
        subject: The subject line.
        body: The plain-text body.
        job_id: Stamped into X-JobPilot-Job, so a retry can find it.
        attachment: An optional (filename, PDF bytes) pair.

    Returns:
        The email as bytes.
    """
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = message_id_for(job_id)
    message["X-JobPilot-Job"] = str(job_id)
    message.set_content(body)
    if attachment is not None:
        filename, data = attachment
        message.add_attachment(data, maintype="application", subtype="pdf", filename=filename)
    return message.as_bytes()


class GmailApi:
    """A minimal Gmail REST client for drafts and the Sent check."""

    def __init__(self, client_id: str, client_secret: str, refresh_token: str) -> None:
        """Create the client from the OAuth app and the stored refresh token."""
        self.client_id, self.client_secret, self.refresh_token = client_id, client_secret, refresh_token
        self._access: str | None = None
        self._expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def _token(self, client: httpx.AsyncClient, rejected: str | None = None) -> str:
        """Return a valid access token, refreshing it when needed.

        Args:
            client: The HTTP client to refresh with.
            rejected: A token Gmail just refused; forces a refresh.

        Raises:
            RuntimeError: The refresh failed (sign in again).
        """
        async with self._token_lock:
            if self._access is None or time.monotonic() >= self._expires_at or self._access == rejected:
                resp = await client.post(
                    TOKEN_URL,
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "refresh_token": self.refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"Gmail token refresh failed ({resp.status_code}) - run `make gmail-auth`"
                    )
                body = resp.json()
                self._access = str(body["access_token"])
                # Refresh a minute early, so a token never expires
                # mid-request.
                self._expires_at = time.monotonic() + max(0, int(body.get("expires_in", 3600)) - 60)
            return self._access

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Make one authorised Gmail API call, refreshing once on a 401."""
        async with httpx.AsyncClient(timeout=30) as client:
            token = await self._token(client)
            response = await client.request(
                method, f"{GMAIL}{path}", headers={"Authorization": f"Bearer {token}"}, **kwargs
            )
            if response.status_code == 401:
                token = await self._token(client, rejected=token)
                response = await client.request(
                    method, f"{GMAIL}{path}", headers={"Authorization": f"Bearer {token}"}, **kwargs
                )
            return response

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """Make an authorised GET call."""
        return await self._request("GET", path, params=params)

    async def _post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        """Make an authorised POST call with a JSON body."""
        return await self._request("POST", path, json=body)

    async def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
        job_id: int,
        attachment: tuple[str, bytes] | None = None,
    ) -> tuple[str, str]:
        """(draft id, thread id). The thread id is how a later retry finds the sent email."""
        # Gmail expects URL-safe base64 without padding.
        raw = base64.urlsafe_b64encode(build_message(to, subject, body, job_id, attachment)).rstrip(b"=")
        response = await self._post("/drafts", {"message": {"raw": raw.decode()}})
        response.raise_for_status()
        body_json = response.json()
        return str(body_json["id"]), str(body_json["message"]["threadId"])

    async def draft_exists(self, draft_id: str) -> bool:
        """Return False if the draft is gone (Gmail deletes it once sent)."""
        response = await self._get(f"/drafts/{draft_id}", {"format": "minimal"})
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    async def find_sent(self, job_id: int, thread_id: str | None) -> str | None:
        """The sent message for this job: in the draft's thread, labelled SENT, carrying X-JobPilot-Job.

        Verified against real Gmail: a sent draft stays in its thread and keeps the custom header,
        while Gmail replaces any custom Message-ID - so rfc822msgid: search can't be used."""
        if not thread_id:
            return None
        response = await self._get(
            f"/threads/{thread_id}", {"format": "metadata", "metadataHeaders": ["X-JobPilot-Job"]}
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        for message in response.json().get("messages", []):
            headers = {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}
            if "SENT" in (message.get("labelIds") or []) and headers.get("x-jobpilot-job") == str(job_id):
                return str(message["id"])
        return None

    async def send_draft(self, draft_id: str) -> str:
        """Send a draft and return the sent message id."""
        response = await self._post("/drafts/send", {"id": draft_id})
        response.raise_for_status()
        return str(response.json()["id"])
