"""Job Chat policy for skills the profile doesn't have.

A request to add a skill the confirmed profile doesn't have is answered
with a profile proposal, never by editing the resume or email.
"""

from __future__ import annotations

import re

from jobpilot.safety import claims

ADD = re.compile(r"\b(add|include|mention|claim|put|list)\b", re.I)


def unconfirmed_skill_request(request: str, facts: dict[int, str]) -> str | None:
    """The first skill the request asks to add that no confirmed profile fact mentions (whole words)."""
    if not ADD.search(request):
        return None
    known = claims.skills_in(" \n ".join(facts.values()))
    for skill_id, name in claims.skills_in(request).items():
        if skill_id not in known:
            return name
    return None
