"""Job preferences (versioned). Keys stored by older versions are ignored when loading."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RelatedTitle(BaseModel):
    """A market title close to your target titles, suggested by the role expander, with its reason."""

    title: str = Field(max_length=120)
    reason: str = Field(default="", max_length=300)


class Preferences(BaseModel):
    """The user's job preferences: where, what, how many, and the bar."""

    locations: list[str] = Field(default_factory=list)
    remote_ok: bool = True
    exp_min_years: float | None = Field(default=None, ge=0)
    exp_max_years: float | None = Field(default=None, ge=0)
    notice_period_days: int | None = Field(default=None, ge=0)
    needs_visa: bool = False
    titles_target: list[str] = Field(default_factory=list)
    titles_avoid: list[str] = Field(default_factory=list)
    # Searched as well as titles_target. Refreshed weekly by the role expander; you can edit the list, and
    # titles you remove go to related_titles_removed so a refresh never brings them back.
    related_titles: list[RelatedTitle] = Field(default_factory=list)
    related_titles_removed: list[str] = Field(default_factory=list)
    related_titles_for: list[str] = Field(default_factory=list)  # the target titles they were made from
    related_titles_at: datetime | None = None
    blacklist: list[str] = Field(default_factory=list)
    whitelist: list[str] = Field(default_factory=list)
    threshold: int = Field(default=70, ge=0, le=100)
    cards_per_day: int = Field(default=5, ge=1, le=50)
    emails_per_day: int = Field(default=2, ge=0, le=20)  # no longer used: no daily email limit (D4)
    # One email per company per 30 days (config outreach.company_window_days). Off by default (D4).
    company_rule: bool = False
    # One daily message listing the top jobs instead of one card message each.
    digest_mode: bool = False

    def search_titles(self) -> list[str]:
        """Every title to search: your targets first, then the related titles (no repeats)."""
        seen: dict[str, str] = {}
        # Compare titles case- and space-insensitively; keep the first
        # spelling seen.
        for title in [*self.titles_target, *(r.title for r in self.related_titles)]:
            seen.setdefault(" ".join(title.lower().split()), title.strip())
        return [t for t in seen.values() if t]
