"""Job Chat: one message in, one reply out.

Resolve the job, build the fenced context, one typed decision, then a
tier-1 draft or answer, or a tier-2 proposal. Any refusal or failure
is answered in the chat - never retried silently - and Job Chat has its
own daily token budget. It works while a run is active; the job's row
lease and version checks keep a chat edit and the run from overwriting
each other.
"""

from __future__ import annotations

import logging
from typing import Any

from jobpilot.chat import context, job_resolver, memory, policy, proposal_service, resume_edit
from jobpilot.core.config import get_config
from jobpilot.core.errors import JobPilotError, VersionConflict
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import emails as emails_repo
from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.queries import profile_items
from jobpilot.db.queries import resume_versions as versions_repo
from jobpilot.db.queries import scores as scores_repo
from jobpilot.db.session import transaction
from jobpilot.hr_email import drafts as email_drafts
from jobpilot.llm.client import LLMClient
from jobpilot.models.email import EmailDraft
from jobpilot.models.job_chat import JobChatDecision
from jobpilot.models.profile import fact_texts
from jobpilot.queue import outbox
from jobpilot.resume import tex_versions
from jobpilot.safety import caps, email_checks, idempotency

log = logging.getLogger(__name__)


async def handle(
    event_id: int,
    text: str,
    llm: LLMClient | None,
    *,
    reply_to_message_id: int | None = None,
) -> None:
    """Handle one Job Chat message and always answer it.

    Args:
        event_id: The stored message event.
        text: The message text.
        llm: The model client, or None when no API key is set.
        reply_to_message_id: The card message it replies to, if any.
    """
    try:
        await _handle(event_id, text, llm, reply_to_message_id=reply_to_message_id)
    except (ValueError, JobPilotError, VersionConflict) as exc:  # refusals and checks: tell the user
        await _plain_reply(event_id, f"I couldn't do that: {exc}")
    except Exception:  # anything else: say so once; the work item is done (no paid retries)
        log.exception("Job Chat failed for event %s", event_id)
        await _plain_reply(event_id, "Something went wrong handling that request; nothing was changed.")


async def _handle(
    event_id: int,
    text: str,
    llm: LLMClient | None,
    *,
    reply_to_message_id: int | None = None,
) -> None:
    """Resolve the job, check the budget, decide, act and reply."""
    async with transaction() as conn:
        resolution = await job_resolver.resolve(conn, text, reply_to_message_id=reply_to_message_id)
        if resolution.job_id is None:
            await _choices(conn, event_id, resolution.choices)
            return
        job_id = resolution.job_id
    request = text[5:].strip() if text.lower().startswith("/chat") else text.strip()
    if not request:
        await _plain_reply(event_id, "Reply with what you want to change or understand about this job.")
        return
    if llm is None:
        await _plain_reply(event_id, "Job Chat needs the OpenAI API key before it can answer.")
        return
    async with transaction() as conn:
        if await caps.job_chat_tokens_left(conn) <= 0:
            await _reply(conn, event_id, "Today's Job Chat budget is used up; it resets at midnight.")
            return
    await memory.remember(job_id, "user", request)
    prompt, facts = await _context(job_id, request)
    # Asked to add a skill the profile lacks: propose it, don't claim
    # it.
    missing_skill = policy.unconfirmed_skill_request(request, facts)
    if missing_skill:
        proposal_id = await proposal_service.create(
            job_id, "update_profile", {"kind": "skill", "text": missing_skill}
        )
        reply = (
            f"{missing_skill} isn't in your confirmed profile, so I can't put it on the resume or email. "
            "If you really have it, tap Confirm to add it to your profile first; then ask again."
        )
        await memory.remember(
            job_id, "assistant", reply, tool_called="update_profile", tier=2, proposal_id=proposal_id
        )
        await _plain_reply(event_id, reply)
        return
    result = await llm.structured(
        "job_chat", prompt, JobChatDecision, max_tokens=get_config().budgets.job_chat_tokens_per_message
    )
    decision = result.output
    provenance = {
        "prompt_version": getattr(result, "prompt_id", None),
        "model_id": getattr(result, "model", None),
    }
    reply, tier, proposal_id, created = await _execute(job_id, decision, facts, provenance, llm, request)
    await memory.remember(
        job_id,
        "assistant",
        reply,
        tool_called=decision.action,
        tier=tier,
        proposal_id=proposal_id,
        version_created=created,
        prompt_version=getattr(result, "prompt_id", None),
        model_id=getattr(result, "model", None),
    )
    await _plain_reply(event_id, reply)


async def _context(job_id: int, request: str):
    """Load the job, drafts, score and profile, and build the prompt.

    Returns:
        (the rendered prompt, confirmed item id -> fact text).
    """
    async with transaction() as conn:
        job = await jobs_repo.get(conn, job_id)
        card = await cards_repo.for_job(conn, job_id)
        resume = await versions_repo.latest(conn, job_id)
        email = await emails_repo.latest(conn, job_id)
        score = await scores_repo.latest_for(conn, job_id)
        items = await profile_items.list_all(conn, statuses=("confirmed",))
    if job is None or card is None:
        raise ValueError("job card not found")
    thread = [dict(row) for row in await memory.load(job_id)]
    facts = fact_texts(items)
    return (
        context.build(
            profile=facts,
            drafts={"resume": _resume_view(resume), "email": dict(email) if email else None},
            thread=thread,
            job={
                "company": job["company"],
                "title": job["title"],
                "description": job["jd_text"],
                "score": dict(score) if score else None,
            },
            request=request,
        ),
        facts,
    )


def _resume_view(resume: Any) -> dict[str, Any] | None:
    """The current resume draft for the model: a resume v2 copy as its plain text (not its LaTeX or every
    edit round), so the context stays small."""
    if resume is None:
        return None
    view = {k: v for k, v in dict(resume).items() if k not in ("tex", "rounds", "doc")}
    view["text"] = tex_versions.plain(resume["tex"]) if resume["tex"] else resume["doc"]
    return view


async def _execute(
    job_id: int,
    decision: JobChatDecision,
    facts: dict[int, str],
    provenance: dict[str, str | None],
    llm: LLMClient,
    request: str,
):
    """Carry out the model's decision.

    Returns:
        (the reply, the tier, a proposal id or None, the version
        created or None).

    Raises:
        ValueError: The edit was refused by the checks.
    """
    action = decision.action
    if action == "edit_resume":
        version = await resume_edit.edit(job_id, decision.resume_operations, llm=llm, instruction=request)
        return (
            f"{decision.reply}\nCreated resume v{version}; review the preview before approving.",
            1,
            None,
            version,
        )
    if action == "edit_email":
        if not decision.email_subject or not decision.email_body:
            raise ValueError("email edit is missing a subject or body")
        async with transaction() as conn:
            current = await emails_repo.latest(conn, job_id)
            card = await cards_repo.for_job(conn, job_id)
            job = await jobs_repo.get(conn, job_id)
        errors = email_checks.check(
            decision.email_body,
            decision.email_citations,
            facts,
            subject=decision.email_subject,
            job_title=str(job["title"]) if job else "",
        )
        if errors:
            raise ValueError("email edit rejected: " + ", ".join(errors))
        if current is None or card is None or card["approved_resume_version_id"] is None:
            raise ValueError("create the first email draft after approving the resume")
        email_id = await email_drafts.create(
            job_id,
            int(card["approved_resume_version_id"]),
            current["to_address"],
            current["to_source"],
            EmailDraft(
                subject=decision.email_subject,
                body=decision.email_body,
                citations=decision.email_citations,
            ),
            prompt_version=provenance["prompt_version"],
            model_id=provenance["model_id"],
        )
        async with transaction() as conn:
            created = await emails_repo.get(conn, email_id)
        email_version = int(created["version"]) if created else None
        return (
            f"{decision.reply}\nCreated email v{email_version}; review it before sending.",
            1,
            None,
            email_version,
        )
    if action in {"update_profile", "change_prefs", "skip_snooze"}:
        proposal_id = await proposal_service.create(job_id, action, decision.proposal)
        return f"{decision.reply}\nNothing changes unless you tap Confirm.", 2, proposal_id, None
    return decision.reply, 1, None, None


async def _choices(conn: Any, event_id: int, choices: list[dict]) -> None:
    """Ask which job is meant, with one button per candidate card."""
    if not choices:
        await _reply(conn, event_id, "No job cards are available for Job Chat yet.")
        return
    buttons = [
        [{"text": f"{row['company']} - {row['title']}"[:55], "callback_data": f"js:{row['callback_token']}"}]
        for row in choices[:3]
    ]
    await outbox.telegram_message(
        conn,
        "Which job do you mean? Select one, then send your request.",
        idempotency_key=idempotency.outbox("chat-choice", event_id),
        reply_markup={"inline_keyboard": buttons},
    )


async def _reply(conn: Any, event_id: int, text: str) -> None:
    """Queue a chat reply in an open transaction."""
    await outbox.telegram_message(conn, text[:4096], idempotency_key=idempotency.outbox("chat", event_id))


async def _plain_reply(event_id: int, text: str) -> None:
    """Queue a chat reply in its own transaction."""
    async with transaction() as conn:
        await _reply(conn, event_id, text)
