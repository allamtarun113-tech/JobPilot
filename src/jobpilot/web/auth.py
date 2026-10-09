"""Single-user login (TOTP) and CSRF protection for the dashboard.

With DASHBOARD_TOTP_SECRET set, every page needs a login (session cookie). Without it the dashboard only
starts on a loopback address and shows a warning banner - fine on the laptop, refused anywhere else.
"""

from __future__ import annotations

import ipaddress
import secrets

from fastapi import HTTPException, Request

from jobpilot.core import clock
from jobpilot.core.config import get_config, get_settings
from jobpilot.safety import totp

PUBLIC_PATHS = {"/login", "/health"}


class CSRFError(HTTPException):
    """A POST without a matching CSRF token (403)."""

    def __init__(self) -> None:
        """Create the 403 error with a reload hint."""
        super().__init__(status_code=403, detail="CSRF check failed - reload the page")


def login_enabled() -> bool:
    """Return True when a TOTP secret is set, so pages need a login."""
    secret = get_settings().dashboard_totp_secret
    return bool(secret and secret.get_secret_value())


def is_loopback(host: str) -> bool:
    """Return True for a loopback address or "localhost"."""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def check_startup(host: str) -> None:
    """Refuse to start beyond localhost without a login secret.

    Raises:
        SystemExit: The host isn't loopback and TOTP isn't configured.
    """
    if not login_enabled() and not is_loopback(host):
        raise SystemExit("Refusing to serve the dashboard beyond localhost without DASHBOARD_TOTP_SECRET.")


def session_secret() -> str:
    """Return the key that signs session cookies.

    Locally a random key per start is fine (sessions just end on
    restart); every serverless instance must share one fixed key.

    Raises:
        RuntimeError: On Vercel without DASHBOARD_SESSION_SECRET.
    """
    secret = get_settings().dashboard_session_secret
    if secret and secret.get_secret_value():
        return secret.get_secret_value()
    if get_settings().serverless:  # every serverless instance must sign cookies with the same key
        raise RuntimeError("DASHBOARD_SESSION_SECRET must be set on Vercel")
    return secrets.token_hex(32)


def misconfigured() -> str | None:
    """On a public deployment (Vercel) the dashboard refuses to serve until login is configured."""
    if get_settings().serverless and not login_enabled():
        return "Dashboard login is not configured (set DASHBOARD_TOTP_SECRET)."
    return None


def is_logged_in(request: Request) -> bool:
    """Return True if the request's session is logged in and not expired."""
    if not login_enabled():
        return True
    if not request.session.get("user"):
        return False
    hours = get_config().login.session_hours
    return int(request.session.get("since", 0)) > clock.now().timestamp() - hours * 3600


async def try_login(request: Request, code: str) -> bool:
    """Check the code under the brute-force / replay guard (raises login_guard.LockedOut when locked)."""
    from jobpilot.db.session import transaction
    from jobpilot.web import login_guard

    secret = get_settings().dashboard_totp_secret
    if not secret:
        return False
    async with transaction() as conn:
        await login_guard.check_allowed(conn)
        counter = totp.matching_counter(secret.get_secret_value(), code)
        if counter is None or not await login_guard.accept_counter(conn, counter):
            await login_guard.record_failure(conn)
            return False
    request.session.clear()
    request.session["user"] = "owner"
    request.session["since"] = int(clock.now().timestamp())
    return True


def csrf_token(request: Request) -> str:
    """Return the session's CSRF token, creating it on first use."""
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(24)
        request.session["csrf"] = token
    return token


async def require_csrf(request: Request) -> None:
    """Dependency for every POST: the form field or X-CSRF-Token header must match the session token."""
    expected = request.session.get("csrf")
    sent = request.headers.get("x-csrf-token")
    if sent is None:
        form = await request.form()
        sent = str(form.get("csrf", ""))
    if not expected or not secrets.compare_digest(expected, sent):
        raise CSRFError()
