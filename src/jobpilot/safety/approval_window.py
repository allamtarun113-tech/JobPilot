"""The email approval window.

An approved send not delivered within ``outreach.reconfirm_after_hours``
(24 h) needs one fresh tap. Shared by the dispatcher (at delivery) and
the scheduler sweep.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from jobpilot.core import clock
from jobpilot.core.config import get_config


def window() -> timedelta:
    """Return how long an email approval stays valid."""
    return timedelta(hours=get_config().outreach["reconfirm_after_hours"])


def expires_at(approved_at: datetime) -> datetime:
    """Return when an approval given at ``approved_at`` expires."""
    return approved_at + window()


def expired(approved_at: datetime) -> bool:
    """Return True if an approval given at ``approved_at`` has expired."""
    return clock.now() >= expires_at(approved_at)
