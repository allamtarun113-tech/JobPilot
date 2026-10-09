"""TOTP login / logout."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from jobpilot.web import auth, login_guard
from jobpilot.web.deps import page, templates

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    """Show the TOTP login form (or skip it when login is off)."""
    if not auth.login_enabled():
        return RedirectResponse("/profile", status_code=303)
    error = (
        "Your login form expired. Enter a new authenticator code to try again. "
        "If this repeats, allow cookies for this site."
        if request.query_params.get("expired") == "1"
        else None
    )
    return templates.TemplateResponse(request, "login.html", page(request, error=error))


@router.post("/login", dependencies=[Depends(auth.require_csrf)])
async def login(request: Request, code: Annotated[str, Form()]) -> Response:
    """Check a TOTP code and start a session.

    Answers 401 for a wrong, expired or reused code and 429 while
    locked out.
    """
    try:
        if await auth.try_login(request, code):
            return RedirectResponse("/jobs", status_code=303)
    except login_guard.LockedOut as exc:
        return templates.TemplateResponse(
            request, "login.html", page(request, error=str(exc)), status_code=429
        )
    return templates.TemplateResponse(
        request, "login.html", page(request, error="Wrong, expired or already used code."), status_code=401
    )


@router.post("/logout", dependencies=[Depends(auth.require_csrf)])
async def logout(request: Request) -> Response:
    """End the session."""
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
