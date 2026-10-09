"""Long-lived job-card states."""

from enum import StrEnum


class CardStatus(StrEnum):
    """Where a job card is in its life, from new to emailed or closed."""

    NEW = "new"
    RESUME_APPROVED = "resume_approved"
    APPLIED = "applied"
    SENDING = "sending"
    NEEDS_RECONFIRM = "needs_reconfirm"
    EMAILED = "emailed"
    SKIPPED = "skipped"
    POSTING_CLOSED = "posting_closed"
