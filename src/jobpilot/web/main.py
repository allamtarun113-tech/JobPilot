"""Dashboard process (FastAPI + Jinja + HTMX).

Production: deployed on Vercel (serverless, public URL) - TOTP login is mandatory there and the app refuses to
serve without it. Development: `python -m jobpilot.web.main` on 127.0.0.1:8765, login optional on loopback.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.middleware.sessions import SessionMiddleware

from jobpilot.core.config import get_config, get_settings
from jobpilot.core.logging import setup_logging
from jobpilot.monitoring.tracing import setup_tracing
from jobpilot.web import auth
from jobpilot.web.routes import health, jobs, login, preferences, prepare, profile, resume, runs, settings


def create_app() -> FastAPI:
    """Build the dashboard app: login and security middleware, then the routes."""
    if get_settings().serverless:  # Vercel imports the app directly; main() never runs there
        setup_logging("http")
        setup_tracing("http")
    app = FastAPI(title="JobPilot", docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(auth.CSRFError)
    async def csrf_error(request: Request, exc: auth.CSRFError) -> Response:
        """Answer a failed CSRF check; an expired login form gets a fresh one."""
        if request.url.path == "/login":
            # Reject the submission and obtain a fresh form; never authenticate it.
            return RedirectResponse("/login?expired=1", status_code=303)
        return JSONResponse({"detail": exc.detail}, status_code=403)

    @app.middleware("http")
    async def require_login(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Require login on every non-public page and add security headers."""
        if (problem := auth.misconfigured()) is not None:
            return PlainTextResponse(problem, status_code=503)
        if request.url.path not in auth.PUBLIC_PATHS and not auth.is_logged_in(request):
            return RedirectResponse("/login", status_code=303)
        response = await call_next(request)
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers["Cache-Control"] = "no-store"
        return response

    # Added last so it runs first: the session is available to the login middleware.
    app.add_middleware(
        SessionMiddleware,
        secret_key=auth.session_secret(),
        # Allow top-level navigation back from Vercel's authentication redirect.
        # Every state-changing request still requires a session-bound CSRF token.
        same_site="lax",
        https_only=get_settings().serverless,
        session_cookie="jobpilot_session",
        max_age=get_config().login.session_hours * 3600,
    )
    for r in (health, login, jobs, prepare, profile, resume, preferences, runs, settings):
        app.include_router(r.router)
    return app


app = create_app()


def main() -> None:
    """Serve the dashboard locally with uvicorn (HTTP_HOST / HTTP_PORT)."""
    setup_logging("http")
    setup_tracing("http")
    host = os.environ.get("HTTP_HOST", "127.0.0.1")
    auth.check_startup(host)
    uvicorn.run(app, host=host, port=int(os.environ.get("HTTP_PORT", "8765")), log_level="warning")


if __name__ == "__main__":
    main()
