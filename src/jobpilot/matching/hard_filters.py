"""Hard filters on the snippet: plain rules with logged reasons, run before anything expensive.

Location and experience are not filters (decision 27): location only orders the jobs (sources/queue_rank.py),
and experience is left to the score.
"""

from __future__ import annotations

import re
from typing import Any

from jobpilot.models.preferences import Preferences
from jobpilot.reference import normalize


def check(job: dict[str, Any], prefs: Preferences) -> str | None:
    """Return the reason to drop this job, or None to keep it.

    Only the skip list and the titles to avoid drop a job; a company on
    the always-consider list is kept regardless of title.

    Args:
        job: The job row (company_norm, title_norm).
        prefs: The user's preferences.
    """
    company = job["company_norm"]
    if company and any(normalize.company_norm(b) == company for b in prefs.blacklist):
        return "company is on your skip list"
    if company and any(normalize.company_norm(w) == company for w in prefs.whitelist):
        return None  # always considered
    title = job["title_norm"]
    for avoid in prefs.titles_avoid:
        phrase = normalize.title_norm(avoid)
        # Whole words only: "intern" must not drop "internal tools".
        if phrase and re.search(rf"\b{re.escape(phrase)}\b", title):
            return f"title matches '{avoid}' (titles to avoid)"
    return None
