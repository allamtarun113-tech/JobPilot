"""HR email (decision 33).

Every new card gets a draft automatically (decision D3), kept quietly until you tap Email HR:
1. The address: a hiring address in the job post first, then one already used for this company, then the
   Job Finder agent's web search (hiring-email task). Code keeps only addresses on the company's own domain,
   ranked hiring inbox > general inbox; a named person is shown with its source and never used by default.
   With no usable address, the draft waits for you to reply with one.
2. The email (decision 36): code writes the subject, greeting, opening, closing and signature; the Email
   Writer adds 2-3 sentences, each a faithful statement of one experience or project fact (checked by code;
   a refused sentence is retried once, then falls back to the fact or is dropped).
3. Tapping Email HR (after approving the resume and marking the job applied) shows that draft at once, with
   the exact approved resume attached. Nothing is sent until you tap Send.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import emails as emails_repo
from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.queries import profile_items
from jobpilot.db.queries import resume_versions as versions_repo
from jobpilot.db.queries import scores as scores_repo
from jobpilot.db.session import transaction
from jobpilot.hr_email import address_finder, drafts, email_writer
from jobpilot.integrations.vectors import VectorStore
from jobpilot.llm.client import LLMClient
from jobpilot.models.email import EmailDraft
from jobpilot.models.profile import fact_texts, parent_ids
from jobpilot.safety import email_checks

log = logging.getLogger(__name__)
# The Job Finder agent's hiring-email task, wired in by the worker: (company, job id) -> [{email, source}].
ContactFinder = Callable[[str, int], Awaitable[list[dict[str, str]]]]
# Words that say nothing about which company a domain belongs to.
COMPANY_FILLER = {
    "the", "and", "of", "pvt", "private", "ltd", "limited", "llp", "inc", "llc", "corp", "corporation", "co",
    "company", "group", "india", "global", "services", "service", "solutions", "technologies", "technology",
    "tech", "systems", "software", "labs", "consulting", "international", "enterprises", "ites", "it",
}  # fmt: skip


def company_keys(company: str) -> list[str]:
    """Distinctive name fragments a company's own domain should contain: 'El-shaddai Recruitment And Ites
    Services' -> ['elshaddai', 'elshaddairecruitment']. Short or generic names give none (too risky)."""
    joined = (re.sub(r"[^a-z0-9]", "", w) for w in company.lower().split())  # 'El-shaddai' -> 'elshaddai'
    words = [w for w in joined if w and w not in COMPANY_FILLER]
    keys = []
    if words and len(words[0]) >= 4:
        keys.append(words[0])
    if len(words) >= 2:
        keys.append(words[0] + words[1])
    return keys


def _host(url_or_email: str) -> str:
    """Return the domain of a URL or an email address, without www. or dashes."""
    host = url_or_email.rsplit("@", 1)[-1] if "@" in url_or_email else urlsplit(url_or_email).netloc
    return host.lower().removeprefix("www.").replace("-", "")


def belongs(host: str, keys: list[str]) -> bool:
    """The domain carries the company's distinctive name (hirevault.com for HireVault, not hireson.com)."""
    flat = host.replace(".", "")
    return any(k in flat for k in keys)


def verified(candidates: list[dict[str, str]], company: str) -> list[address_finder.Address]:
    """The addresses the agent saw that belong to the company's own domain, classified (hiring inbox,
    general inbox or named person). Anything on another domain is dropped."""
    keys = company_keys(company)
    if not keys:
        return []
    found: list[address_finder.Address] = []
    for candidate in candidates:
        for address in address_finder.find(candidate["email"], candidate.get("source") or "web search"):
            if belongs(_host(address.email), keys):
                found.append(address)
    return list({a.email: a for a in found}.values())


def choose(addresses: list[address_finder.Address]) -> address_finder.Address | None:
    """A hiring inbox first; else the company's general inbox (info@, contact@), labelled as such in the
    preview you approve. A named person is never chosen by default."""
    hiring = [a for a in addresses if a.send_default]
    if hiring:
        return hiring[0]
    general = [a for a in addresses if not a.named_person]
    if general:
        a = general[0]
        return address_finder.Address(a.email, f"company's general inbox - {a.source}", False, True)
    return None


async def find_address(
    job: Any, contacts: ContactFinder | None
) -> tuple[address_finder.Address | None, list[address_finder.Address]]:
    """(the address to use or None, everything found - for naming a person you could write to)."""
    addresses = address_finder.find(job["jd_text"], "the job post")
    # Fallbacks run only while no hiring inbox has been found.
    if not any(a.send_default for a in addresses):
        async with transaction() as conn:
            known = await emails_repo.known_address(conn, job["company_norm"])
        if known is not None:
            addresses += address_finder.find(known["to_address"], f"used before for {job['company']}")
    if not any(a.send_default for a in addresses) and contacts is not None:
        addresses += verified(await contacts(str(job["company"]), int(job["id"])), str(job["company"]))
    return choose(addresses), addresses


def sources_of(items: list[Any]) -> tuple[dict[int, str], dict[int, str], list[int], dict[int, str]]:
    """From the confirmed profile rows: (experience / project fact id -> text, role id -> headline,
    the ids among those facts that are about a project, skill id -> name)."""
    facts = fact_texts(items)
    kinds = {int(r["id"]): str(r["kind"]) for r in items}
    parents = parent_ids(items)
    sources = {i: t for i, t in facts.items() if kinds.get(i) in ("achievement", "metric", "project", "role")}
    roles = {i: t for i, t in facts.items() if kinds.get(i) == "role"}
    projects = [i for i in sources if kinds.get(i) == "project" or kinds.get(parents.get(i, -1)) == "project"]
    skills = {int(r["id"]): str(r["text"]) for r in items if r["kind"] == "skill"}
    return sources, roles, projects, skills


async def _write(
    llm: LLMClient, job: Any, items: list[Any], version: Any, score: Any
) -> tuple[EmailDraft, Any]:
    """Write a draft that passes every email check, retrying once.

    Args:
        llm: The model client.
        job: The job row.
        items: Confirmed profile item rows.
        version: The resume version that will be attached (its header gives the name and contacts).
        score: The job's score (for the "why you fit" lines).

    Returns:
        (the checked draft, the model call's result).

    Raises:
        ValueError: The second draft still failed its checks.
    """
    title = str(job["title"])
    facts = fact_texts(items)
    sources, roles, project_ids, skills = sources_of(items)
    header = ((version["doc"] or {}).get("header") or {}) if version else {}
    applicant = str(header.get("name") or "")
    contacts = [str(c.get("text") or "") for c in header.get("contacts") or [] if c.get("text")]
    fit = list(((score["fit"] if score else None) or {}).get("why") or [])
    feedback, errors = "none", []
    for attempt in (1, 2):  # one retry, told exactly what the checks refused
        draft, result, refused = await email_writer.write(
            llm, dict(job), sources, feedback=feedback, applicant=applicant,
            contacts=contacts, roles=roles, fit=fit, project_ids=project_ids, skills=skills,
        )  # fmt: skip
        draft.citations = email_checks.complete_citations(draft.body, draft.citations, facts, title)
        errors = email_checks.check(
            draft.body, draft.citations, facts, subject=draft.subject, job_title=title
        )
        model_refused = [r for r in refused if not r.startswith("no project sentence")]
        if not errors and (not model_refused or attempt == 2):
            return draft, result
        log.info("email draft %s for job %s: refused %s; failed %s", attempt, job["id"], refused, errors)
        feedback = "; ".join([*refused, *errors])
    raise ValueError("The drafted email failed its checks: " + "; ".join(errors))


async def compose(
    job_id: int,
    llm: LLMClient,
    *,
    auto: bool = False,
    address_override: str | None = None,
    allow_named_person: bool = False,
    contacts: ContactFinder | None = None,
    vectors: VectorStore | None = None,
) -> int:
    """Write or show the HR email for a job.

    Args:
        job_id: The job.
        llm: The model client.
        auto: Write the automatic draft for a new card (stored, not
            shown). Otherwise show the job's draft - the prepared one
            when there is one, re-attached to the approved resume - or
            write it now.
        address_override: An address the user typed.
        allow_named_person: Allow writing to a named person found.
        contacts: The Job Finder's hiring-email search.
        vectors: Unused since decision 36 (skill notes no longer go into the email); kept for callers.

    Returns:
        The email id (0 when an automatic draft had nothing to attach).

    Raises:
        ValueError: The card isn't ready, or no address was found.
    """
    async with transaction() as conn:
        job = await jobs_repo.get(conn, job_id)
        card = await cards_repo.for_job(conn, job_id)
        items = await profile_items.list_all(conn, statuses=("confirmed",))
        score = await scores_repo.latest_for(conn, job_id)
        latest = await emails_repo.latest(conn, job_id)
        approved = (
            await versions_repo.get(conn, card["approved_resume_version_id"])
            if card and card["approved_resume_version_id"]
            else None
        )
        newest = await versions_repo.latest(conn, job_id)
    if job is None or card is None:
        raise ValueError("job card not found")
    if auto:
        if latest is not None or newest is None:
            return int(latest["id"]) if latest is not None else 0  # one draft per job; nothing to attach yet
        auto_address, _found = await find_address(job, contacts)
        draft, result = await _write(llm, job, list(items), newest, score)
        to, source = (
            (auto_address.email, auto_address.source)
            if auto_address
            else ("", f"no hiring email found for {job['company']}")
        )
        return await drafts.store(
            job_id, int(newest["id"]), to, source, draft,
            status=drafts.PREPARED if auto_address else drafts.NEEDS_ADDRESS,
            prompt_version=getattr(result, "prompt_id", None), model_id=getattr(result, "model", None),
        )  # fmt: skip
    if card["status"] != "applied" or approved is None:
        raise ValueError("approve the resume and mark the job applied first")
    prepared = latest if latest is not None and latest["status"] in drafts.WAITING else None
    found: list[address_finder.Address] = []
    address: address_finder.Address | None
    if address_override:
        given = address_finder.find(address_override, "you gave it")
        if not given:
            raise ValueError("enter a valid email address")
        address = given[0]
    elif prepared is not None and prepared["to_address"]:
        address = address_finder.Address(prepared["to_address"], prepared["to_source"], False, True)
    else:
        address, found = await find_address(job, contacts)
        if address is None and allow_named_person:
            address = next((a for a in found if a.named_person), None)
    if address is None:
        named = [a for a in found if a.named_person]
        hint = (
            f" I found a named contact, {named[0].email} (source: {named[0].source}); reply with it if you"
            " want to write to them."
            if named
            else ""
        )
        raise ValueError(
            f"I searched the web but found no hiring email for {job['company']}. Reply to this card with"
            " the HR email address and I'll write the email." + hint
        )
    resume_id = int(approved["id"])
    if (
        prepared is not None
        and prepared["resume_version_id"] == resume_id
        and prepared["to_address"] == address.email
    ):
        return await drafts.release(int(prepared["id"]))  # shown as written
    if prepared is not None:  # same text, now with your approved resume and the address to use
        draft = EmailDraft(
            subject=prepared["subject"], body=prepared["body"], citations=list(prepared["citations"])
        )
        meta = {"prompt_version": prepared["prompt_version"], "model_id": prepared["model_id"]}
    else:
        draft, result = await _write(llm, job, list(items), approved, score)
        meta = {
            "prompt_version": getattr(result, "prompt_id", None),
            "model_id": getattr(result, "model", None),
        }
    return await drafts.create(job_id, resume_id, address.email, address.source, draft, **meta)
