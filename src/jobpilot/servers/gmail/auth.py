"""Caller authentication and scope checks for the Gmail MCP server.

Also holds the encrypted refresh-token file.
"""

from __future__ import annotations

import hmac
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet

from jobpilot.core.config import REPO_ROOT, get_settings
from jobpilot.integrations.mcp_scopes import allowed
from jobpilot.safety.mcp_keys import caller_key

TOKEN_FILE = REPO_ROOT / "var" / "gmail_token.enc"


class Refused(Exception):
    """The server refuses the call (bad caller, scope, or setup)."""

    pass


def authenticated_caller() -> str:
    """Return the verified caller name from the subprocess environment.

    Raises:
        Refused: Unknown caller or wrong caller key.
    """
    caller = os.environ.get("MCP_CALLER", "")
    if caller not in ("worker", "dispatcher"):
        raise Refused(f"unknown caller {caller!r}")
    # Constant-time comparison, so timing can't reveal the key.
    if not hmac.compare_digest(os.environ.get("MCP_CALLER_KEY", ""), caller_key("gmail", caller)):
        raise Refused(f"bad key for caller {caller!r}")
    return caller


def check_scope(tool: str) -> None:
    """Raise Refused unless the verified caller may call ``gmail.<tool>``."""
    caller = authenticated_caller()
    if tool not in allowed(caller, "gmail"):
        raise Refused(f"caller {caller!r} may not call gmail.{tool}")


def _fernet() -> Fernet:
    """Return the Fernet cipher for the token file (GMAIL_TOKEN_KEY)."""
    key = get_settings().gmail_token_key
    if key is None or not key.get_secret_value():
        raise Refused("GMAIL_TOKEN_KEY is not set")
    return Fernet(key.get_secret_value().encode())


def save_token(data: dict[str, str], path: Path = TOKEN_FILE) -> None:
    """Encrypt and save the OAuth token data, readable by the owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_fernet().encrypt(json.dumps(data).encode()))
    path.chmod(0o600)


def load_token(path: Path = TOKEN_FILE) -> dict[str, str] | None:
    """Decrypt and return the saved token data, or None if not signed in."""
    if not path.exists():
        return None
    return dict(json.loads(_fernet().decrypt(path.read_bytes())))
