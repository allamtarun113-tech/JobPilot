"""The Telegram card (decision 32).

Formatted with Telegram HTML in clear sections, plus short-id,
exact-version buttons (64-byte limit).
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

from jobpilot.core import clock
from jobpilot.matching.gap_resources import for_skill

# Only absolute http(s) URLs with a dotted host become links.
_HTTP = re.compile(r"^https?://[^\s/]+\.[^\s/]+")


def jd_summary(requirements: dict[str, Any] | None, job: dict[str, Any]) -> str:
    """A one-line JD summary from the extracted requirements (deterministic; no extra model call)."""
    snippet = (
        " (JD not found - snippet only)"
        if job.get("jd_lookup") == "not_found"
        else " (snippet only)"
        if job.get("jd_is_snippet")
        else ""
    )
    if not requirements:
        return f"JD{snippet}: {str(job.get('jd_text') or '')[:120].strip()}"
    must = ", ".join(requirements.get("must_skills", [])[:4]) or "no hard requirements listed"
    lo, hi = requirements.get("years_min"), requirements.get("years_max")
    years = (
        f"{lo:g}-{hi:g} yrs"
        if lo is not None and hi is not None
        else f"{lo:g}+ yrs"
        if lo is not None
        else ""
    )
    domain = str(requirements.get("domain") or "")[:50]
    parts = [p for p in (f"needs {must}", years, domain) if p]
    return f"JD{snippet}: " + "; ".join(parts)


def _e(value: object) -> str:
    """Text for Telegram HTML: job data can contain < > & and must never become markup."""
    return html.escape(str(value), quote=False)


def _link(url: str, label: str) -> str:
    """Return an HTML link for a safe URL, else just the escaped label."""
    return (
        f'<a href="{html.escape(url, quote=True)}">{_e(label)}</a>' if _HTTP.match(url or "") else _e(label)
    )


def text(
    job: dict[str, Any],
    score: dict[str, Any],
    *,
    dashboard_url: str,
    requirements: dict[str, Any] | None = None,
    note: str | None = None,
    boost: dict[str, Any] | None = None,
) -> str:
    """The card in Telegram HTML (send with parse_mode HTML): role and company, where and when, the match and
    why, the top skills to learn with a free link each, the job in one line, and the dashboard link."""
    location = job.get("location_raw") or job.get("city") or "location not stated"
    posted = job.get("posted_at") or job.get("first_seen_at")
    days = max(0, (clock.now() - posted).days) if isinstance(posted, datetime) else 0
    company = job.get("company") or "Unknown company"
    fit = score.get("fit") or {}
    match = f"Match {float(score['score']):g}%"
    if boost and boost.get("added"):
        match = f"Match {boost['before']:g}% → {boost['after']:g}% with the tailored resume"
    snippet = " · scored on a snippet" if job.get("jd_is_snippet") else ""
    lines = [
        f"<b>{_e(job['title'])}</b>",
        f"🏢 {_e(company)}",
        f"📍 {_e(location)} · posted {days}d ago",
        "",
        f"🎯 <b>{_e(match)}</b>{_e(snippet)}",
    ]
    if fit.get("summary"):
        lines.append(_e(fit["summary"]))
        lines += [f"✓ {_e(why)}" for why in (fit.get("why") or [])[:3]]
    else:  # scores from before rubric v3
        parts = sorted(score["parts"].items(), key=lambda pair: -float(pair[1]))[:2]
        lines.append(_e(", ".join(f"{name} {float(points):g}" for name, points in parts)))
    gaps = score["gap"][:3]
    if gaps:
        lines += ["", "📚 <b>To learn</b>"]
        for gap in gaps:
            quick = " · quick to learn" if gap.get("easy") else ""
            resources = for_skill(gap.get("skill_id"), 1)
            link = f" - {_link(resources[0]['url'], 'free course')}" if resources else ""
            lines.append(f"• {_e(gap['skill'])} +{float(gap['points']):g}{_e(quick)}{link}")
    lines += [
        "",
        f"📝 {_e(jd_summary(requirements, job))}",
        _link(f"{dashboard_url.rstrip('/')}/jobs/{job['id']}", "Open full details on the dashboard"),
    ]
    if note:
        lines += ["", f"<i>{_e(note)}</i>"]
    return "\n".join(lines)[:4096]


def _apply_button(apply_url: str) -> list[dict[str, str]]:
    """Return the Apply URL button, or nothing for a malformed URL."""
    # Telegram rejects malformed URL buttons (that would block the card); the link stays on the dashboard.
    return [{"text": "Open apply link", "url": apply_url}] if _HTTP.match(apply_url or "") else []


def buttons(token: str, resume_version: int, apply_url: str) -> dict[str, Any]:
    """Approve resume vN + Open apply link, then View PDF + More..."""
    first = [
        {"text": f"✅ Approve resume v{resume_version}", "callback_data": f"ra:{token}:{resume_version}"}
    ]
    first += [{**b, "text": "🔗 Apply"} for b in _apply_button(apply_url)]
    return {
        "inline_keyboard": [
            first,
            [
                {"text": f"📄 View resume v{resume_version}", "callback_data": f"rv:{token}"},
                {"text": "More…", "callback_data": f"mo:{token}:{resume_version}"},
            ],
        ]
    }


def more_buttons(token: str, resume_version: int, apply_url: str) -> dict[str, Any]:
    """The More... menu: I applied, Email HR, Prepare, Skip (and back)."""
    first = [
        {"text": f"✅ Approve resume v{resume_version}", "callback_data": f"ra:{token}:{resume_version}"}
    ]
    first += [{**b, "text": "🔗 Apply"} for b in _apply_button(apply_url)]
    return {
        "inline_keyboard": [
            first,
            [
                {"text": "I applied", "callback_data": f"ap:{token}"},
                {"text": "✉️ Email HR", "callback_data": f"em:{token}"},
            ],
            [
                {"text": "📘 Prepare", "callback_data": f"pr:{token}"},
                {"text": "Skip", "callback_data": f"sk:{token}"},
            ],
            [{"text": "« Back", "callback_data": f"mb:{token}:{resume_version}"}],
        ]
    }
