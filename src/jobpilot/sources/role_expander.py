"""Role expansion: related market titles for the Job Finder to search.

One model call lists market titles close to your target titles ("AI
Engineer" -> "ML Engineer", "GenAI Engineer", ...), so the Job Finder
searches all of them. Refreshed weekly or when your target titles
change, never on every run. Titles you removed are never suggested
again.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from jobpilot.llm import prompts
from jobpilot.llm.client import LLMClient
from jobpilot.models.preferences import Preferences, RelatedTitle
from jobpilot.reference import normalize

REFRESH_AFTER = timedelta(days=7)
TITLES_WANTED = 15


class RelatedTitles(BaseModel):
    """The role expander's output: related titles with reasons."""

    titles: list[RelatedTitle] = Field(default_factory=list, max_length=30)


def _key(title: str) -> str:
    """Return the comparison key of a title (its normalized form)."""
    return normalize.title_norm(title) or title.lower().strip()


def needs_refresh(prefs: Preferences, now: datetime) -> bool:
    """Return True if the related titles are stale or were made for other targets."""
    if not prefs.titles_target:
        return False
    if [_key(t) for t in prefs.related_titles_for] != [_key(t) for t in prefs.titles_target]:
        return True
    return prefs.related_titles_at is None or now - prefs.related_titles_at >= REFRESH_AFTER


def merge(prefs: Preferences, suggested: list[RelatedTitle], now: datetime) -> Preferences:
    """Keep the titles you already have, add new suggestions; never re-add a removed or avoided title."""
    blocked = {_key(t) for t in [*prefs.titles_target, *prefs.related_titles_removed]}
    avoid = [phrase for t in prefs.titles_avoid if (phrase := normalize.title_norm(t))]

    def allowed(title: str) -> bool:  # same word rule as the "titles to avoid" filter
        """Return True unless the title is a target, removed, or avoided."""
        key = _key(title)
        return (
            bool(key)
            and key not in blocked
            and not any(re.search(rf"\b{re.escape(a)}\b", key) for a in avoid)
        )

    kept = [r for r in prefs.related_titles if allowed(r.title)]
    have = {_key(r.title) for r in kept}
    for item in suggested:
        key = _key(item.title)
        if allowed(item.title) and key not in have:
            kept.append(RelatedTitle(title=" ".join(item.title.split()), reason=item.reason.strip()))
            have.add(key)
    new = prefs.model_copy(deep=True)
    # Keep a bounded list; older kept titles come first.
    new.related_titles = kept[: TITLES_WANTED * 2]
    new.related_titles_for = list(prefs.titles_target)
    new.related_titles_at = now
    return new


async def suggest(llm: LLMClient, prefs: Preferences, *, run_id: int | None = None) -> list[RelatedTitle]:
    """Ask the model for titles related to the user's targets.

    Args:
        llm: The model client.
        prefs: The user's preferences (targets, removed and avoided).
        run_id: The run the tokens count against, if any.

    Returns:
        The suggested titles (not yet merged).
    """
    prompt = prompts.render(
        "sources/role_expansion",
        count=TITLES_WANTED,
        targets=", ".join(prefs.titles_target),
        avoid=", ".join([*prefs.related_titles_removed, *prefs.titles_avoid]) or "(none)",
    )
    result = await llm.structured("role_expansion", prompt, RelatedTitles, run_id=run_id, max_tokens=2000)
    return result.output.titles
