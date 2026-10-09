"""Find published email addresses, preferring generic hiring inboxes (careers@, jobs@, hr@ ...).

An address whose local part is a hiring word (as a whole token: 'hr', 'hr.india', 'careers-in') is a hiring
inbox and may be used by default. Anything else that looks like a person ('chris.mathew@', 'shreya@') is a
named individual: shown with its source and defaulting to don't send.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

EMAIL = re.compile(r"(?<![\w.+-])([\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,})(?![\w.-])")
HIRING_TOKEN = re.compile(
    r"^(careers?|jobs?|hiring|recruit(?:ing|ment|er|ers)?|talent(?:acquisition)?|ta|hr|people|"
    r"humanresources|resumes?|cv|apply|applications?)$"
)
GENERIC = {"info", "contact", "hello", "support", "admin", "office", "team", "enquiries", "inquiries"}
NO_REPLY = re.compile(r"^(no-?reply|do-?not-?reply|notifications?|mailer-daemon)", re.I)


@dataclass(frozen=True)
class Address:
    """One email address found, with where it came from and how it may be used."""

    email: str
    source: str
    named_person: bool
    send_default: bool


def _tokens(local: str) -> list[str]:
    """Split an address's local part into words ("hr.india" -> hr, india)."""
    return [t for t in re.split(r"[._+-]+", local.lower()) if t]


def classify(email: str, source: str) -> Address | None:
    """Classify one address as a hiring inbox, a general inbox or a person.

    Args:
        email: The address.
        source: Where it was found.

    Returns:
        The classified address, or None for a no-reply address.
    """
    local = email.split("@", 1)[0].lower()
    if NO_REPLY.match(local):
        return None
    tokens = _tokens(local)
    hiring = any(HIRING_TOKEN.match(t) for t in tokens)
    generic = local in GENERIC
    named = not hiring and not generic
    return Address(email, source, named, hiring)


def find(text: str, source: str) -> list[Address]:
    """Find and classify every address in a text.

    Args:
        text: Any text (a JD, a web page).
        source: Where the text came from.

    Returns:
        The addresses, hiring inboxes first and named people last.
    """
    found = []
    for email in dict.fromkeys(x.lower() for x in EMAIL.findall(text or "")):
        if (address := classify(email, source)) is not None:
            found.append(address)
    return sorted(found, key=lambda x: (not x.send_default, x.named_person, x.email))
