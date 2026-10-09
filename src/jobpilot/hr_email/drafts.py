"""Store checked email drafts and show them for approval.

Persists a checked email, requests Gmail draft creation (with the
approved resume attached) through the outbox, then sends the preview
with Send vN / Edit / Skip.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from jobpilot.core import clock
from jobpilot.core.config import get_config, get_settings
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import emails
from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.queries import preferences as prefs_repo
from jobpilot.db.queries import resume_versions as versions_repo
from jobpilot.db.session import transaction
from jobpilot.models.email import EmailDraft
from jobpilot.queue import outbox
from jobpilot.safety import idempotency

# Card statuses in which an email can be drafted.
EDITABLE = {"applied", "needs_reconfirm"}
# A draft written automatically for a new card (decision 33): kept quietly until you tap Email HR.
PREPARED, NEEDS_ADDRESS = "prepared", "needs_address"
WAITING = {PREPARED, NEEDS_ADDRESS}


async def company_rule_on(conn: Any) -> bool:
    """The one-email-per-company-per-30-days rule is a preference, off by default (decision D4)."""
    row = await prefs_repo.get(conn)
    return bool(row and row["data"].get("company_rule"))


async def store(
    job_id: int,
    resume_version_id: int,
    to: str,
    source: str,
    draft: EmailDraft,
    *,
    status: str = "draft",
    prompt_version: str | None = None,
    model_id: str | None = None,
) -> int:
    """Save a checked draft as the job's next email version (no Gmail draft, no message yet)."""
    async with transaction() as conn:
        card = await cards_repo.for_job(conn, job_id)
        job = await jobs_repo.get(conn, job_id)
        if card is None or job is None:
            raise ValueError("job has no card")
        if await emails.sent_for_job(conn, job_id):
            raise ValueError("an email was already sent for this job")
        window = get_config().outreach["company_window_days"]
        if await company_rule_on(conn) and await emails.company_sent_since(
            conn, job["company_norm"], clock.now() - timedelta(days=window)
        ):
            raise ValueError(f"an email was already sent to this company in the last {window} days")
        current = await emails.latest(conn, job_id)
        return await emails.create(
            conn,
            job_id=job_id,
            resume_version_id=resume_version_id,
            version=int(current["version"] if current else 0) + 1,
            to_address=to,
            to_source=source,
            subject=draft.subject,
            body=draft.body,
            citations=draft.citations,
            status=status,
            prompt_version=prompt_version,
            model_id=model_id,
        )


async def create(
    job_id: int,
    resume_version_id: int,
    to: str,
    source: str,
    draft: EmailDraft,
    *,
    prompt_version: str | None = None,
    model_id: str | None = None,
) -> int:
    """Store a checked draft and show it at once.

    Args:
        job_id: The job.
        resume_version_id: The approved resume version to attach.
        to: The recipient.
        source: Where the address came from.
        draft: The checked draft.
        prompt_version: The writer prompt that produced it.
        model_id: The model that produced it.

    Returns:
        The email id.
    """
    email_id = await store(
        job_id, resume_version_id, to, source, draft, prompt_version=prompt_version, model_id=model_id
    )
    await release(email_id)
    return email_id


async def release(email_id: int) -> int:
    """Show a stored draft: request the Gmail draft (approved resume attached) through the outbox, then the
    preview with Send vN / Edit / Skip."""
    async with transaction() as conn:
        email = await emails.get(conn, email_id)
        if email is None:
            raise ValueError("email not found")
        job_id = int(email["job_id"])
        card = await cards_repo.for_job(conn, job_id)
        if card is None:
            raise ValueError("job has no card")
        if card["status"] not in EDITABLE:
            raise ValueError("mark the job applied first (or wait for the current send to finish)")
        # The email must carry exactly the resume version the user
        # approved.
        if card["approved_resume_version_id"] != email["resume_version_id"]:
            raise ValueError("email must use the exact approved resume version")
        if not email["to_address"]:
            raise ValueError("this draft has no address yet")
        job = await jobs_repo.get(conn, job_id)
        resume = await versions_repo.get(conn, email["resume_version_id"])
        if job is None or resume is None:
            raise ValueError("job or approved resume not found")
        # A quietly prepared draft becomes a normal draft once shown.
        if email["status"] in WAITING:
            await emails.set_status(conn, email_id, "draft", email["lock_version"])
        to, source, version = email["to_address"], email["to_source"], email["version"]
        subject, body = email["subject"], email["body"]
        partition = outbox.card_partition(card["id"])
        await outbox.enqueue(
            conn,
            partition_key=partition,
            kind="gmail",
            payload={
                "op": "create_draft",
                "email_id": email_id,
                "to": to,
                "subject": subject,
                "body": body,
                "job_id": job_id,
                "attachment_key": resume["pdf_key"],
            },
            idempotency_key=idempotency.outbox("email", email_id, "draft"),
        )
        dashboard = f"{get_settings().dashboard_base_url.rstrip('/')}/jobs/{job_id}"
        preview = (
            f"Email preview v{version}\nTo: {to} (from {source})\nSubject: {subject}\n"
            f"Attached: resume v{resume['version']}\n\n{body}\n\n"
            f"Edit: {dashboard} or reply to change it."
        )
        token = card["callback_token"]
        await outbox.telegram_message(
            conn,
            preview,
            partition_key=partition,
            idempotency_key=idempotency.outbox("email", email_id, "preview"),
            reply_markup={
                "inline_keyboard": [
                    [
                        {"text": f"Send v{version}", "callback_data": f"es:{token}:{email_id}"},
                        {"text": "Edit", "callback_data": f"ee:{token}:{email_id}"},
                        {"text": "Skip", "callback_data": f"ek:{token}:{email_id}"},
                    ]
                ]
            },
        )
        return email_id
