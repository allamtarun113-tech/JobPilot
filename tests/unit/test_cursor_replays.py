"""Cursor replays: the jobs-API date bucket covers gaps of 1, 3, 10 days and 3 weeks."""

from __future__ import annotations

from datetime import timedelta

import pytest

from jobpilot.core import clock
from jobpilot.sources.jobs_api import date_bucket


@pytest.mark.parametrize(("days", "bucket"), [(1, "today"), (3, "3days"), (10, "month"), (21, "month")])
def test_jobs_api_bucket_covers_the_gap(days: int, bucket: str) -> None:
    """The date bucket always covers the time since the last run."""
    assert date_bucket(clock.now() - timedelta(days=days, minutes=-1)) == bucket
