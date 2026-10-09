"""Preferences form (versioned: a stale form is refused, never merged).

A form with a mistake is shown again with everything you typed and the
error on top; nothing is saved and nothing typed is lost.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core.errors import VersionConflict
from jobpilot.db.session import transaction
from jobpilot.models.preferences import Preferences, RelatedTitle
from jobpilot.profile import preferences
from jobpilot.web import auth
from jobpilot.web.deps import db, flash, page, record_action, templates

router = APIRouter()


def _list(value: str) -> list[str]:
    """Split a comma- or newline-separated field into trimmed items."""
    return [x.strip() for x in value.replace("\n", ",").split(",") if x.strip()]


def _num(value: str, label: str) -> float | None:
    """Parse an optional decimal field.

    Raises:
        ValueError: Not a number (the message names the field).
    """
    try:
        return float(value) if value.strip() else None
    except ValueError:
        raise ValueError(f"{label} must be a number (you typed '{value}')") from None


def _int(value: str, label: str) -> int | None:
    """Parse an optional whole-number field.

    Raises:
        ValueError: Not a number (the message names the field).
    """
    if not value.strip():
        return None
    try:
        return int(float(value))
    except ValueError:
        raise ValueError(f"{label} must be a whole number (you typed '{value}')") from None


def _int_or(value: str, label: str, default: int) -> int:
    """Parse a whole-number field, using ``default`` when empty."""
    parsed = _int(value, label)
    return default if parsed is None else parsed


_LISTS = ("locations", "titles_target", "titles_avoid", "blacklist", "whitelist")


def _related(typed: str, current: Preferences) -> tuple[list[RelatedTitle], list[str]]:
    """The related titles as you left them (one per line; reasons kept), and the titles you removed, which a
    weekly refresh must not bring back."""
    reasons = {r.title.lower(): r.reason for r in current.related_titles}
    titles = list(dict.fromkeys(t.strip() for t in typed.splitlines() if t.strip()))
    kept = [RelatedTitle(title=t, reason=reasons.get(t.lower(), "added by you")) for t in titles]
    wanted = {t.lower() for t in titles}
    removed = [r.title for r in current.related_titles if r.title.lower() not in wanted]
    removed_before = [t for t in current.related_titles_removed if t.lower() not in wanted]
    return kept, list(dict.fromkeys([*removed_before, *removed]))


_CHECKS = ("remote_ok", "digest_mode", "needs_visa", "company_rule")


def _as_typed(f: dict[str, str]) -> Any:
    """The submitted form, shaped like Preferences, so the page can show it again exactly as typed."""
    values: dict[str, Any] = {k: v for k, v in f.items() if k not in ("csrf", "nonce", "version")}
    for name in _LISTS:
        values[name] = _list(f.get(name, ""))
    for name in _CHECKS:
        values[name] = name in f
    for name in ("exp_min_years", "exp_max_years", "notice_period_days"):
        values[name] = f.get(name, "").strip() or None
    values["related_titles"] = [
        SimpleNamespace(title=t.strip(), reason="")
        for t in f.get("related_titles", "").splitlines()
        if t.strip()
    ]
    return SimpleNamespace(**values)


def _friendly(exc: ValidationError) -> str:
    """Turn pydantic validation errors into one readable line."""
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'form'}: {e['msg']}" for e in exc.errors())


@router.get("/preferences", response_class=HTMLResponse)
async def prefs_page(request: Request, conn: Annotated[AsyncConnection, Depends(db)]) -> Response:
    """Show the preferences form with the current version."""
    prefs, version = await preferences.load(conn)
    await conn.commit()
    return templates.TemplateResponse(
        request, "preferences.html", page(request, prefs=prefs, version=version)
    )


@router.post("/preferences", dependencies=[Depends(auth.require_csrf)])
async def save_prefs(request: Request) -> Response:
    """Validate and save the preferences form (version-checked).

    On any error the form comes back with the typed values and the
    error, and nothing is saved.
    """
    form = await request.form()
    f = {k: str(v) for k, v in form.items()}
    error: str | None = None
    try:
        async with transaction() as conn:
            current, _ = await preferences.load(conn)
        related, removed = _related(f.get("related_titles", ""), current)
        prefs = Preferences(
            locations=_list(f.get("locations", "")),
            remote_ok="remote_ok" in f,
            digest_mode="digest_mode" in f,
            exp_min_years=_num(f.get("exp_min_years", ""), "Experience min years"),
            exp_max_years=_num(f.get("exp_max_years", ""), "Experience max years"),
            notice_period_days=_int(f.get("notice_period_days", ""), "Notice period"),
            needs_visa="needs_visa" in f,
            titles_target=_list(f.get("titles_target", "")),
            titles_avoid=_list(f.get("titles_avoid", "")),
            related_titles=related,
            related_titles_removed=removed,
            related_titles_for=current.related_titles_for,
            related_titles_at=current.related_titles_at,
            blacklist=_list(f.get("blacklist", "")),
            whitelist=_list(f.get("whitelist", "")),
            threshold=_int_or(f.get("threshold", ""), "Match threshold", 70),
            cards_per_day=_int_or(f.get("cards_per_day", ""), "Cards per day", 5),
            company_rule="company_rule" in f,
        )
        if not 0 <= prefs.threshold <= 100:
            raise ValueError("Match threshold must be between 0 and 100")
        if (
            prefs.exp_min_years is not None
            and prefs.exp_max_years is not None
            and prefs.exp_min_years > prefs.exp_max_years
        ):
            raise ValueError("Experience min years is more than max years")
        async with transaction() as conn:
            if await record_action(
                conn, "preferences.save", f.get("nonce", ""), {"version": f.get("version")}
            ):
                await preferences.save(conn, prefs, int(f["version"]))
                flash(request, "Preferences saved.")
    except VersionConflict:
        error = (
            "Preferences changed elsewhere (maybe /prefs on Telegram). Your entries are below; save again."
        )
    except ValidationError as exc:
        error = f"Not saved: {_friendly(exc)}"
    except ValueError as exc:
        error = f"Not saved: {exc}"
    if error is None:
        return RedirectResponse("/preferences", status_code=303)
    async with transaction() as conn:
        _, version = await preferences.load(conn)
    return templates.TemplateResponse(
        request,
        "preferences.html",
        page(request, prefs=_as_typed(f), version=version, form_error=error),
        status_code=400,
    )
