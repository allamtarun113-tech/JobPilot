"""Slash commands. /start and /resume enqueue a run request; every reply goes through the outbox."""

from __future__ import annotations

from jobpilot.bot import messages
from jobpilot.cards import reminders
from jobpilot.core import clock
from jobpilot.core.errors import VersionConflict
from jobpilot.db.queries import outbox as outbox_repo
from jobpilot.db.queries import runs as runs_repo
from jobpilot.db.queries import work_items as work_repo
from jobpilot.db.queries.base import versioned_update
from jobpilot.db.session import transaction
from jobpilot.db.tables import runs
from jobpilot.models.work import RUN_REQUEST
from jobpilot.monitoring import metrics
from jobpilot.profile import preferences
from jobpilot.queue import jobs_queue, outbox
from jobpilot.safety import caps, idempotency, killswitch
from jobpilot.sources import queue_rank


def _reply_key(event_id: int) -> str:
    """Return the outbox key of the single reply to a command event."""
    return idempotency.outbox("reply", event_id)


async def handle(event_id: int, text: str) -> None:
    """Run a slash command (unknown commands get the help text).

    Args:
        event_id: The stored command event.
        text: The message text, e.g. "/start 5".
    """
    parts = text.strip().split()
    # "/start@JobPilotBot" in groups: drop the bot name.
    command = parts[0].split("@", 1)[0].lower()
    args = parts[1:]
    if command == "/start":
        await start(event_id, args)
    elif command == "/stop":
        await stop(event_id)
    elif command == "/status":
        await status(event_id)
    elif command == "/prefs":
        await prefs(event_id, args)
    elif command == "/pause":
        await pause(event_id)
    elif command == "/resume":
        await resume(event_id)
    elif command == "/digest":
        await digest(event_id)
    elif command == "/killswitch":
        await kill_switch(event_id, args)
    else:
        await _reply(event_id, messages.HELP)


async def _reply(event_id: int, text: str, reply_markup: dict | None = None) -> None:
    """Send the reply to a command in its own transaction."""
    async with transaction() as conn:
        await outbox.telegram_message(
            conn, text, idempotency_key=_reply_key(event_id), reply_markup=reply_markup
        )


async def pause(event_id: int) -> None:
    """Hold the active run after its current job (the run graph checks this between jobs)."""
    for _ in range(3):
        try:
            async with transaction() as conn:
                run = await runs_repo.get_active(conn)
                if run is None:
                    text = messages.no_active_run()
                else:
                    if not run["pause_requested"]:
                        await versioned_update(conn, runs, run["id"], run["version"], pause_requested=True)
                    text = f"Pausing run #{run['id']} after the current job."
                await outbox.telegram_message(conn, text, idempotency_key=_reply_key(event_id))
                return
        except VersionConflict:
            continue


async def resume(event_id: int) -> None:
    """Queue a request to continue the paused run (/resume)."""
    async with transaction() as conn:
        await jobs_queue.enqueue(
            conn,
            kind=RUN_REQUEST,
            payload={"resume": True, "request_key": f"event-{event_id}"},
            idempotency_key=idempotency.work_for_event(event_id, RUN_REQUEST),
        )


async def digest(event_id: int) -> None:
    """Send the digest: cards waiting, queued jobs and jobs below the bar."""
    async with transaction() as conn:
        text, markup = await reminders.digest(conn)
    await _reply(event_id, text, markup)


async def kill_switch(event_id: int, args: list[str]) -> None:
    """/killswitch on|off - the global stop flag (runs refuse to start; email sending is held)."""
    choice = args[0].lower() if args else ""
    async with transaction() as conn:
        if choice == "on":
            await killswitch.engage(conn, "manual")
            text = "Kill switch ON: no runs start and no email is sent until /killswitch off."
        elif choice == "off":
            await killswitch.release(conn)
            text = "Kill switch OFF."
        else:
            state = "ON" if await killswitch.is_engaged(conn) else "OFF"
            text = f"Kill switch is {state}. Use /killswitch on|off."
        await outbox.telegram_message(conn, text, idempotency_key=_reply_key(event_id))


async def start(event_id: int, args: list[str]) -> None:
    """Queue a run request (/start [cap]); the worker takes the lease.

    Args:
        event_id: The command event (makes a repeat a no-op).
        args: An optional card cap.
    """
    cap: int | None = None
    if args:
        if not args[0].isdigit() or int(args[0]) < 1:
            await _reply(event_id, messages.bad_cap(args[0]))
            return
        cap = int(args[0])
    request_key = f"event-{event_id}"
    async with transaction() as conn:
        await jobs_queue.enqueue(
            conn,
            kind=RUN_REQUEST,
            payload={"card_cap": cap, "request_key": request_key},
            idempotency_key=idempotency.work_for_event(event_id, RUN_REQUEST),
        )


async def stop(event_id: int) -> None:
    """Ask the active run to drain (/stop), or end a paused run."""
    for _ in range(3):
        try:
            async with transaction() as conn:
                run = await runs_repo.get_active(conn)
                if run is None:
                    paused = await runs_repo.get_paused(conn)
                    if paused is not None:  # /stop on a paused run ends it
                        await runs_repo.finish_paused(conn, paused["id"])
                        text = messages.run_summary(
                            paused["id"],
                            "stop_requested",
                            dict(paused["counts"] or {}),
                            0,
                            int(paused["tokens_in"] + paused["tokens_out"]),
                            float(paused["cost_usd"]),
                            [],
                        )
                    else:
                        text = messages.no_active_run()
                    await outbox.telegram_message(conn, text, idempotency_key=_reply_key(event_id))
                    return
                if not run["stop_requested"]:
                    await versioned_update(conn, runs, run["id"], run["version"], stop_requested=True)
                await outbox.telegram_message(
                    conn, messages.stopping(run["id"]), idempotency_key=_reply_key(event_id)
                )
                return
        except VersionConflict:
            continue  # the run moved on; re-read and try again
    await _reply(event_id, messages.no_active_run())


async def status(event_id: int) -> None:
    """Reply with what the current run is doing, queues and recall."""
    async with transaction() as conn:
        latest = await runs_repo.latest(conn, limit=1)
        queue = await work_repo.count_by_status(conn)
        lag = await outbox_repo.lag(conn)
        waiting = len(await queue_rank.ranked(conn, clock.now()))
        hits, shared, _ = await metrics.recall(conn)
        tokens_today = await caps.tokens_used_today(conn)
        await outbox.telegram_message(
            conn,
            messages.status(latest[0] if latest else None, queue, lag, waiting, hits, shared)
            + f"\nTokens today: {tokens_today:,}"
            + ("\nKill switch: ON" if await killswitch.is_engaged(conn) else ""),
            idempotency_key=_reply_key(event_id),
        )


async def prefs(event_id: int, args: list[str]) -> None:
    """/prefs shows preferences; /prefs <what> <value> changes one (version-checked, user-typed only)."""
    for _ in range(3):
        try:
            async with transaction() as conn:
                current, version = await preferences.load(conn)
                if not args:
                    text = messages.prefs_summary(current)
                else:
                    try:
                        new, change = preferences.apply_command(current, args)
                    except ValueError as exc:
                        text = messages.prefs_error(str(exc))
                    else:
                        await preferences.save(conn, new, version)
                        text = messages.prefs_changed(change.field, change.before, change.after)
                await outbox.telegram_message(conn, text, idempotency_key=_reply_key(event_id))
                return
        except VersionConflict:
            continue
