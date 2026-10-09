"""Request dependencies for the dashboard: database connection, templates, page context, action events."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core.config import REPO_ROOT, get_config, get_settings
from jobpilot.db.session import get_engine
from jobpilot.queue import events
from jobpilot.web import auth

templates = Jinja2Templates(directory=str(REPO_ROOT / "templates" / "ui"))


async def db() -> AsyncIterator[AsyncConnection]:
    """A read connection for one request."""
    async with get_engine().connect() as conn:
        yield conn


def page(request: Request, **context: Any) -> dict[str, Any]:
    """Common template context: CSRF token, a fresh nonce for double-submit protection, login banner."""
    return {
        "csrf": auth.csrf_token(request),
        "nonce": uuid.uuid4().hex,
        "login_disabled": not auth.login_enabled(),
        "flash": request.session.pop("flash", None),
        "env_label": "Cloud" if get_settings().serverless else "Local",
        "shadow_mode": get_config().shadow_mode,
        **context,
    }


def flash(request: Request, message: str) -> None:
    """Store a one-time message to show on the next page."""
    request.session["flash"] = message


async def record_action(
    conn: AsyncConnection, action: str, nonce: str, payload: dict[str, Any]
) -> int | None:
    """Record a dashboard action as an event (like a tap) and return its id. None if this nonce was already
    used (a double submit), in which case the caller does nothing."""
    return await events.append(
        conn,
        type=f"dashboard.{action}",
        payload=payload,
        idempotency_key=f"dashboard:{nonce or uuid.uuid4().hex}",
    )
