"""Jobs the user shares to the bot.

A link plus (ideally) the pasted JD. Processed immediately, no cursor;
each share is counted for recall (a hit if JobPilot already had the
job).
"""

from __future__ import annotations

import re

from jobpilot.models.job import JobPosting, SourceKind

URL = re.compile(r"https?://[^\s<>\"']+")
_AT_COMPANY = re.compile(r"\b(?:at|@)\s+([A-Z][\w&.,' -]{1,60}?)(?:\s+[-|(,]|\s+in\s|\n|$)")
_COMPANY_LINE = re.compile(r"^(?:company|employer|organi[sz]ation)\s*[:\-]\s*(.+)$", re.I | re.M)
_LOCATION_LINE = re.compile(r"^(?:location|city|job location)\s*[:\-]\s*(.+)$", re.I | re.M)


def find_url(text: str) -> str | None:
    """Return the first http(s) URL in the text, without trailing punctuation."""
    m = URL.search(text)
    return m.group(0).rstrip(".,)") if m else None


def posting_from_share(text: str, event_id: int) -> JobPosting | None:
    """Best-effort title / company / location from a shared message. The LLM extraction refines them later."""
    url = find_url(text)
    if url is None:
        return None
    body = URL.sub(" ", text).strip()
    # The first non-empty line is usually the job title.
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    title = lines[0][:120] if lines else "Shared job"
    company = ""
    if m := _COMPANY_LINE.search(body):
        company = m.group(1).strip()
    elif m := _AT_COMPANY.search(lines[0] if lines else ""):
        company = m.group(1).strip()
        title = lines[0][: m.start()].strip(" -|,") or title
    location = m.group(1).strip() if (m := _LOCATION_LINE.search(body)) else None
    return JobPosting(
        source=SourceKind.SHARED,
        external_id=f"share:{event_id}",
        title=title,
        company=company,  # left empty when unknown - never a shared placeholder that merges different jobs
        location=location,
        url=url,
        description=body,
        description_is_snippet=len(body) < 400,
        raw={"event_id": event_id},
    )
