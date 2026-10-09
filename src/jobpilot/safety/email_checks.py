"""Deterministic outreach checks: cited claims only, no salary talk, no false urgency, under the word limit.

(One email per job and one per company per 30 days are enforced where drafts and sends are created.)
"""

from __future__ import annotations

import re

from jobpilot.core.config import get_config
from jobpilot.safety import claims, fidelity
from jobpilot.safety.citations import supported_numbers

SALARY = re.compile(r"\b(salary|ctc|compensation|package|lpa|lakhs?|stipend|pay\s+expectations?)\b|₹", re.I)
PLACEHOLDER = re.compile(r"\[[^\]]{1,40}\]|<[^>]{1,40}>|\{[^}]{1,40}\}")
# The sign-off and contact lines are the applicant's own details, not claims: checks stop there.
SIGN_OFF = re.compile(
    r"^\s*(best regards|kind regards|regards|sincerely|thank you|thanks),?\s*$", re.I | re.M
)
URGENCY = re.compile(
    r"\b(urgent(ly)?|immediately|asap|last chance|act now|time[- ]sensitive|at the earliest|right away)\b",
    re.I,
)


def content(body: str) -> str:
    """The email without its sign-off and contact lines."""
    m = SIGN_OFF.search(body)
    return body[: m.start()] if m else body


def complete_citations(
    body: str, citations: list[int], facts: dict[int, str], job_title: str = ""
) -> list[int]:
    """Add citations for skills or titles the body names but didn't cite.

    Works like the resume rewrite check. Unbacked claims still fail
    ``check``.

    Args:
        body: The email body.
        citations: Item ids the writer cited.
        facts: Confirmed item id -> fact text.
        job_title: The job's title (naming it is not a claim).

    Returns:
        The citations, then the added ids, without repeats.
    """
    cited = [facts[i] for i in citations if i in facts]
    extra = claims.backing_items(content(body), [*cited, job_title], facts)
    return list(dict.fromkeys([*citations, *extra]))


def check(
    body: str,
    citations: list[int],
    facts: dict[int, str],
    max_words: int | None = None,
    *,
    subject: str = "",
    job_title: str = "",
) -> list[str]:
    """Run every outreach check on an email.

    Args:
        body: The email body.
        citations: Profile item ids the email cites.
        facts: Confirmed item id -> fact text.
        max_words: The word limit; defaults to the config value.
        subject: The subject line (checked for salary and urgency).
        job_title: The job's title (naming it is not a claim).

    Returns:
        The problems found; an empty list means the email passes.
    """
    limit = max_words or get_config().outreach["max_email_words"]
    body = content(body)
    errors = []
    cited = [facts[i] for i in citations if i in facts]
    if any(i not in facts for i in citations):
        errors.append("unknown citation")
    if not citations:
        errors.append("no cited profile facts (every claim must cite the profile)")
    if not supported_numbers(body, cited):
        errors.append("uncited number")
    if problems := claims.unsupported(body, [*cited, job_title]):
        errors.append("uncited claim: " + ", ".join(problems))
    if len(body.split()) > limit:
        errors.append(f"over {limit} words")
    if SALARY.search(body) or SALARY.search(subject):
        errors.append("salary talk")
    if URGENCY.search(body) or URGENCY.search(subject):
        errors.append("false urgency")
    said = set(fidelity.words(" ".join(cited)))
    if hype := sorted(set(fidelity.words(body)) & fidelity.HYPE - said):
        errors.append(f"puffery ('{hype[0]}')")
    if PLACEHOLDER.search(body) or PLACEHOLDER.search(subject):
        errors.append("placeholder text like [Name] left in")
    return errors
