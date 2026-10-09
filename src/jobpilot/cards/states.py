"""The card state machine: every status change goes through `require()`.

new -> resume_approved -> applied -> (sending -> emailed | needs_reconfirm), plus skipped and posting_closed.
A new resume version (chat edit, late full JD) moves new / resume_approved back to new. Outcomes (replied,
interview, rejected, offer) are recorded alongside the status once you have applied.
"""

from __future__ import annotations

from jobpilot.models.card import CardStatus

# Every move a card may make; a status maps to the statuses it may
# move to.
ALLOWED: dict[CardStatus, set[CardStatus]] = {
    CardStatus.NEW: {
        CardStatus.NEW,
        CardStatus.RESUME_APPROVED,
        CardStatus.SKIPPED,
        CardStatus.POSTING_CLOSED,
    },
    CardStatus.RESUME_APPROVED: {
        CardStatus.NEW,
        CardStatus.APPLIED,
        CardStatus.SKIPPED,
        CardStatus.POSTING_CLOSED,
    },
    CardStatus.APPLIED: {CardStatus.SENDING, CardStatus.POSTING_CLOSED},
    CardStatus.SENDING: {CardStatus.EMAILED, CardStatus.NEEDS_RECONFIRM, CardStatus.APPLIED},
    CardStatus.NEEDS_RECONFIRM: {CardStatus.SENDING, CardStatus.APPLIED},
    CardStatus.EMAILED: set(),
    CardStatus.SKIPPED: set(),
    CardStatus.POSTING_CLOSED: set(),
}
OUTCOMES = ("replied", "interview", "rejected", "offer")
OUTCOME_STATES = {
    CardStatus.APPLIED,
    CardStatus.SENDING,
    CardStatus.NEEDS_RECONFIRM,
    CardStatus.EMAILED,
    CardStatus.POSTING_CLOSED,
}
NO_ACTION_STATES = (CardStatus.NEW.value, CardStatus.RESUME_APPROVED.value)


class IllegalTransition(ValueError):
    """A card status change the state machine doesn't allow."""

    pass


def permits(before: str, after: str) -> bool:
    """Return True if a card may move from ``before`` to ``after``."""
    return CardStatus(after) in ALLOWED[CardStatus(before)]


def require(before: str, after: str) -> None:
    """Raise IllegalTransition unless a card may move from ``before`` to ``after``."""
    if not permits(before, after):
        raise IllegalTransition(
            f"This card is {before.replace('_', ' ')}; it can't move to {after.replace('_', ' ')}."
        )


def outcome_allowed(status: str, outcome: str) -> bool:
    """Return True if this outcome may be recorded for a card in this status."""
    return outcome in OUTCOMES and CardStatus(status) in OUTCOME_STATES
