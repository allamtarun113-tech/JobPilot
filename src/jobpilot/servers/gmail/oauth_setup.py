"""One-time Gmail sign-in (installed-app OAuth with a loopback redirect). Stores the refresh token encrypted.

Usage: make gmail-auth   (needs GMAIL_OAUTH_CLIENT_ID, GMAIL_OAUTH_CLIENT_SECRET, GMAIL_TOKEN_KEY in .env)
Scopes: gmail.readonly (read alerts) + gmail.compose - nothing else.
Publish the Google OAuth app to Production: refresh tokens of apps left in Testing expire after 7 days.
"""

from __future__ import annotations

import http.server
import secrets
import sys
import time
import urllib.parse
import webbrowser

import httpx

from jobpilot.core.config import get_settings
from jobpilot.servers.gmail.auth import save_token

SCOPES = "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.compose"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def callback_result(path: str, state: str) -> dict[str, str]:
    """Read Google's redirect back to the loopback server.

    Args:
        path: The request path with its query string.
        state: The anti-forgery state sent with the sign-in link.

    Returns:
        ``{"code": ...}`` or ``{"error": ...}``; empty for a stale or
        forged callback.
    """
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(path).query))
    if not secrets.compare_digest(query.get("state", ""), state):
        return {}
    if query.get("error"):
        return {"error": query["error"]}
    if query.get("code"):
        return {"code": query["code"]}
    return {}


def main() -> None:
    """Run the one-time sign-in and store the refresh token encrypted.

    Opens the Google consent page, waits up to ten minutes for the
    redirect to a local port, then swaps the code for tokens.
    """
    s = get_settings()
    if not (s.gmail_oauth_client_id and s.gmail_oauth_client_secret and s.gmail_token_key):
        sys.exit("Set GMAIL_OAUTH_CLIENT_ID, GMAIL_OAUTH_CLIENT_SECRET and GMAIL_TOKEN_KEY in .env first.")
    state = secrets.token_urlsafe(16)
    result: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        """Answers the single redirect from Google's sign-in page."""

        def do_GET(self) -> None:
            """Record the code (or error) and show a short note in the browser."""
            callback = callback_result(self.path, state)
            if "code" in callback:
                result.update(callback)
                body = b"JobPilot: authorization received. Return to Codex to check the connection."
            elif "error" in callback:
                result.update(callback)
                body = b"JobPilot: Google denied authorization. Return to Codex to retry."
            else:
                body = b"JobPilot: outdated sign-in callback. Use the newest Google sign-in tab."
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            """Silence the default per-request logging."""
            pass

    # Port 0: the OS picks a free port for the redirect.
    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{httpd.server_port}/"
    params = {
        "client_id": s.gmail_oauth_client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        # Ask for consent every time so Google always returns a refresh
        # token.
        "prompt": "consent",
        "state": state,
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    print("Opening the Google sign-in page. If it does not open, visit:\n" + url)
    httpd.timeout = 1
    webbrowser.open(url)
    deadline = time.monotonic() + 600
    try:
        while not result and time.monotonic() < deadline:
            httpd.handle_request()
    finally:
        httpd.server_close()
    if "error" in result:
        sys.exit("Google denied Gmail authorization. Check the test user and approve the requested access.")
    if "code" not in result:
        sys.exit("Gmail sign-in timed out. Restart make gmail-auth and use the newest sign-in tab.")
    resp = httpx.post(
        TOKEN_URL,
        data={
            "code": result["code"],
            "client_id": s.gmail_oauth_client_id,
            "client_secret": s.gmail_oauth_client_secret.get_secret_value(),  # type: ignore[union-attr]
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
        },
    )
    resp.raise_for_status()
    token = resp.json()
    if "refresh_token" not in token:
        sys.exit(
            "Google returned no refresh token - remove JobPilot's access in your Google account and retry."
        )
    save_token({"refresh_token": token["refresh_token"]})
    print("Gmail connected. Token stored encrypted in var/gmail_token.enc")


if __name__ == "__main__":
    main()
