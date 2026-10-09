"""The single source of 'now'. Tests replace it to simulate gaps, expiry and stale leases."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

# The active clock. A module-level callable so tests can swap it out.
_now: Callable[[], datetime] = lambda: datetime.now(UTC)  # noqa: E731


def now() -> datetime:
    """Return the current aware UTC time from the active clock."""
    return _now()


def set_clock(fn: Callable[[], datetime]) -> None:
    """Replace the active clock.

    Args:
        fn: A zero-argument callable returning an aware datetime.
    """
    global _now
    _now = fn


def reset_clock() -> None:
    """Restore the real UTC wall clock after a test."""
    set_clock(lambda: datetime.now(UTC))


class FrozenClock:
    """A controllable clock for tests: clock = FrozenClock(); set_clock(clock); clock.advance(seconds=130)."""

    def __init__(self, start: datetime | None = None) -> None:
        """Start the clock at ``start``, or at the real current time.

        Args:
            start: The initial time; defaults to now in UTC.
        """
        self.current = start or datetime.now(UTC)

    def __call__(self) -> datetime:
        """Return the frozen time, so the instance works as a clock."""
        return self.current

    def advance(self, **kwargs: float) -> None:
        """Move the clock forward.

        Args:
            **kwargs: Any ``timedelta`` keyword, e.g. ``seconds=130``.
        """
        self.current += timedelta(**kwargs)
