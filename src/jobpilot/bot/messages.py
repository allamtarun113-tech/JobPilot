"""All user-facing Telegram wording in one place."""

from __future__ import annotations

from typing import Any

STOP_REASONS = {
    "queue_empty": "no more jobs to process",
    "card_cap": "today's card cap was reached",
    "budget": "the daily token budget is used up",
    "api_quota": "the jobs-API quota is used up",
    "stop_requested": "you sent /stop",
    "paused": "you sent /pause",
    "killswitch": "the kill switch is on",
    "error_spike": "too many errors in a row",
    "preflight_failed": "a preflight check failed",
}

HELP = (
    "JobPilot commands:\n"
    "/start [cap] - find jobs posted since the last check (optional max cards)\n"
    "/status - what the current run is doing\n"
    "/stop - finish the current job, then stop and summarize\n"
    "/pause, /resume - hold after the current job; continue the same run\n"
    "/digest - queued jobs, jobs below your bar, and cards waiting for you\n"
    "/killswitch on|off - stop everything (runs and email sending)\n"
    "/prefs - show or change preferences (e.g. /prefs threshold 70)\n"
    "/chat [company or role] - discuss or revise one job card\n"
    "/help - this list"
)


def run_started(run_id: int, card_cap: int) -> str:
    """Return the "run started" message."""
    return f"Run #{run_id} started (up to {card_cap} cards). I'll message you when it's done."


def run_resumed(run_id: int) -> str:
    """Return the message for a crashed run being resumed."""
    return f"Run #{run_id} was interrupted; resuming where it stopped."


def run_continued(run_id: int) -> str:
    """Return the message for a paused run continuing."""
    return f"Run #{run_id} continues where it paused."


def run_paused(run_id: int) -> str:
    """Return the message for a run that paused."""
    return f"Run #{run_id} paused after the current job. Send /resume to continue it, or /stop to end it."


def nothing_paused() -> str:
    """Return the reply to /resume with no paused run."""
    return "No run is paused. Send /start to begin a new one."


def run_already_active(run_id: int, step: str | None) -> str:
    """Return the reply to /start while a run is in progress."""
    return f"Run #{run_id} is already in progress (step: {step or 'starting'}). Use /status or /stop."


SOURCE_LABELS = {"jobs_api": "Jobs API", "job_finder": "Job Finder"}
OUTCOME_LABELS = {
    "queued": "queued",
    "below_bar": "below your bar",
    "filtered": "filtered",
    "screened_out": "screened out",
    "pending_extraction": "waiting for the API key",
    "error": "errors",
}


def run_summary(
    run_id: int, reason: str, counts: dict[str, Any], cards: int, tokens: int, cost: float, notes: list[str]
) -> str:
    """Build the end-of-run summary.

    Args:
        run_id: The run.
        reason: Why it stopped (a StopReason value).
        counts: Per-source and per-outcome counts.
        cards: Cards made.
        tokens: Tokens used.
        cost: Cost in USD (recorded, not shown).
        notes: Notes to list (first six).

    Returns:
        The message text.
    """
    lines = [f"Run #{run_id} finished - {STOP_REASONS.get(reason, reason or 'done')}."]
    for source, label in SOURCE_LABELS.items():
        c = counts.get(source)
        if not c:
            continue
        if c.get("skipped"):
            lines.append(f"{label}: skipped (reason below)")
        elif c.get("error"):
            lines.append(f"{label}: failed")
        else:
            lines.append(f"{label}: {c.get('fetched', 0)} found, {c.get('new', 0)} new")
    pipeline = counts.get("pipeline") or {}
    outcome = ", ".join(f"{n} {OUTCOME_LABELS.get(k, k)}" for k, n in pipeline.items() if n)
    if outcome:
        lines.append(f"Jobs: {outcome}")
    queue = counts.get("queue") or {}
    lines.append(f"Queue: {queue.get('waiting', 0) + pipeline.get('queued', 0)} waiting for cards")
    if cards:
        lines.append(f"Cards: {cards}")
    lines.append(f"Tokens: {tokens:,}")  # no dollar amounts in messages (user decision, 2026-10-09)
    if notes:
        lines.append("")
        lines += [f"- {n}" for n in notes[:6]]
    return "\n".join(lines)


def run_failed(run_id: int, error_type: str) -> str:
    """Return the message for a run that failed with an error."""
    return f"Run #{run_id} stopped because of an error ({error_type}). Details are in the trace."


def stopping(run_id: int) -> str:
    """Return the reply to /stop while a run is active."""
    return f"Stopping run #{run_id} after the current job. A summary follows."


def no_active_run() -> str:
    """Return the reply when no run is in progress."""
    return "No run is in progress. Send /start to begin."


def status(
    run: Any | None,
    queue: dict[str, int],
    outbox_lag: dict[str, Any],
    jobs_waiting: int = 0,
    hits: int = 0,
    shared: int = 0,
) -> str:
    """Build the /status reply.

    Args:
        run: The latest run row, or None.
        queue: Work items per status.
        outbox_lag: Undelivered outbox counts.
        jobs_waiting: Jobs queued for a card.
        hits: Shared jobs already found (30 days).
        shared: Jobs shared (30 days).

    Returns:
        The message text.
    """
    if run is None:
        head = "No run has started yet."
    else:
        active = run["status"] in ("running", "draining", "paused")
        state = (
            f", step {run['current_step']}"
            if active
            else f" ({STOP_REASONS.get(run['stop_reason'] or '', run['stop_reason'])})"
        )
        head = f"Run #{run['id']}: {run['status']}{state}\nTokens: {run['tokens_in'] + run['tokens_out']:,}"
    pending = queue.get("queued", 0) + queue.get("running", 0)
    recall = (
        f"{hits}/{shared} shared jobs already found ({hits / shared:.0%})" if shared else "no shared jobs yet"
    )
    return (
        f"{head}\nJobs waiting for cards: {jobs_waiting}\nRecall (30 days): {recall}\n"
        f"Work items pending: {pending}\nMessages waiting to send: {outbox_lag['undelivered']}"
    )


def bad_cap(arg: str) -> str:
    """Return the reply to /start with a non-numeric cap."""
    return f"'{arg}' isn't a number. Use /start or /start 10."


PREFS_USAGE = (
    "Change a preference:\n"
    "/prefs threshold 75 - minimum match score\n"
    "/prefs cards 8 - cards per day\n"
    "/prefs block Acme / unblock Acme - skip a company\n"
    "/prefs location Pune / unlocation Pune\n"
    "/prefs remote on|off\n"
    "/prefs digest on|off - one daily list instead of one card per job\n"
    "/prefs company on|off - at most one HR email per company per 30 days"
)


def _fmt(value: object) -> str:
    """Format a preference value for display."""
    if value is None or value == []:
        return "not set"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def prefs_summary(prefs: Any) -> str:
    """Return the /prefs reply listing the current preferences."""
    return (
        "Your preferences:\n"
        f"Match threshold: {prefs.threshold}\n"
        f"Cards per day: {prefs.cards_per_day} - one email per company per 30 days: "
        f"{_fmt(prefs.company_rule)}\n"
        f"Locations: {_fmt(prefs.locations)} - remote ok: {_fmt(prefs.remote_ok)}\n"
        f"Titles to target: {_fmt(prefs.titles_target)}\n"
        f"Blocked companies: {_fmt(prefs.blacklist)}\n\n" + PREFS_USAGE
    )


def prefs_changed(field: str, before: object, after: object) -> str:
    """Return the confirmation of one preference change."""
    return f"Updated {field}: {_fmt(before)} -> {_fmt(after)}"


def prefs_error(error: str) -> str:
    """Return the reply for a /prefs command that couldn't be applied."""
    return f"Not changed: {error}\n\n{PREFS_USAGE}" if error != "usage" else PREFS_USAGE


def share_no_link() -> str:
    """Return the reply to a shared message with no link."""
    return (
        "I couldn't find a job link in that message. Send the link, and paste the job description if you can."
    )


def share_result(hit: bool, title: str, company: str, status: str, score: float | None) -> str:
    """Return the reply after a shared job was processed.

    Args:
        hit: Whether JobPilot already had the job.
        title: The job title.
        company: The company.
        status: What happened (queued, carded_now, below_bar, ...).
        score: The match score, if scored.
    """
    seen = "I already had this one" if hit else "New to me - thanks, that helps me measure what I miss"
    scored = f"scored {score:g}" if score is not None else "scored"
    outcome = {
        "queued": f"{scored}, queued for a card",
        "carded_now": f"{scored} - its card is on the way",
        "queued_cap": f"{scored}; today's card cap is reached, so it waits in the queue",
        "card_error": f"{scored}, but building the card failed (it stays queued)",
        "below_bar": f"{scored}, below your bar",
        "carded": "it already has a card",
        "filtered": "it was filtered out",
        "pending_extraction": "saved; scoring waits for the API key",
        "unconfirmed": "saved; confirm your profile on the dashboard to score it",
        "error": "saved, but scoring failed (see the trace)",
    }.get(status, status)
    return f"{seen}: {title} at {company} - {outcome}."
