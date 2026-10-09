"""Brute-force and replay protection for the TOTP login (the dashboard is on a public URL).

- After `login.max_failures` wrong codes within `login.window_minutes`, every login attempt is refused until
  the window passes (a single-user app: one global counter, so rotating IP addresses doesn't help).
- A code that was already used (same or an earlier 30-second step) is refused.
State lives in Postgres (system_flags) because serverless instances share nothing else.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core import clock
from jobpilot.core.config import get_config
from jobpilot.db.queries import flags

KEY = "login_guard"


class LockedOut(Exception):
    """Too many wrong codes recently; login is refused until ``until``."""

    def __init__(self, until: datetime) -> None:
        """Create the error, saying when login opens again."""
        super().__init__(f"too many wrong codes; try again after {until:%H:%M} UTC")
        self.until = until


async def _state(conn: AsyncConnection) -> dict:
    """Return the stored guard state (recent failures, last used step)."""
    return dict(await flags.get(conn, KEY) or {})


async def check_allowed(conn: AsyncConnection) -> None:
    """Raise LockedOut if the recent wrong codes reached the limit."""
    cfg = get_config().login
    state = await _state(conn)
    window_start = clock.now() - timedelta(minutes=cfg.window_minutes)
    failures = [datetime.fromisoformat(t) for t in state.get("failures", [])]
    recent = [t for t in failures if t > window_start]
    if len(recent) >= cfg.max_failures:
        raise LockedOut(min(recent) + timedelta(minutes=cfg.window_minutes))


async def record_failure(conn: AsyncConnection) -> None:
    """Record one wrong code, dropping failures older than the window."""
    state = await _state(conn)
    window_start = clock.now() - timedelta(minutes=get_config().login.window_minutes)
    failures = [t for t in state.get("failures", []) if datetime.fromisoformat(t) > window_start]
    failures.append(clock.now().isoformat())
    await flags.put(conn, KEY, {**state, "failures": failures})


async def accept_counter(conn: AsyncConnection, counter: int) -> bool:
    """False if this code (time step) or a later one was already used - a replay."""
    state = await _state(conn)
    if counter <= int(state.get("last_counter", -1)):
        return False
    # A good code clears the failures and remembers its time step.
    await flags.put(conn, KEY, {"failures": [], "last_counter": counter})
    return True
