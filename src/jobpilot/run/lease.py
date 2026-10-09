"""The single run lease: a unique partial index on runs (status running|draining) plus a heartbeat.

acquire() either starts a new run, takes over a stale one (to resume it from its checkpoint), or
reports that a live run already holds the lease. The worker's scheduler calls heartbeat().
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from jobpilot.bot import messages
from jobpilot.core import clock
from jobpilot.core.config import get_config
from jobpilot.db.queries import runs as runs_repo
from jobpilot.db.session import transaction
from jobpilot.queue import outbox
from jobpilot.safety import idempotency

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Acquired:
    """The lease is ours: a new run, a resumed stale run or a continued pause."""

    run_id: int
    card_cap: int
    resumed: bool
    from_pause: bool = False


@dataclass(frozen=True)
class Held:
    """A live run already holds the lease."""

    run: RowMapping


@dataclass(frozen=True)
class NothingPaused:
    """/resume found no paused run to continue."""

    pass


async def acquire(
    holder: str, card_cap: int, request_key: str, *, resume_only: bool = False
) -> Acquired | Held | NothingPaused:
    """Take the lease. `request_key` makes the 'started' message idempotent per run request.

    A paused run is continued (same run) instead of starting a new one; `resume_only` (/resume) never
    starts a new run."""
    ttl = get_config().lease.run_ttl_seconds
    try:
        async with transaction() as conn:
            paused = await runs_repo.get_paused(conn, for_update=True)
            if paused is not None and await runs_repo.get_active(conn) is None:
                if await runs_repo.resume_paused(conn, paused["id"], holder):
                    await outbox.telegram_message(
                        conn,
                        messages.run_continued(paused["id"]),
                        partition_key=outbox.run_partition(paused["id"]),
                        idempotency_key=idempotency.outbox("run", paused["id"], "continued", request_key),
                    )
                    return Acquired(paused["id"], paused["card_cap"], resumed=True, from_pause=True)
            if resume_only:
                active = await runs_repo.get_active(conn)
                return Held(active) if active is not None else NothingPaused()
            active = await runs_repo.get_active(conn, for_update=True)
            if active is not None:
                if not runs_repo.is_stale(active, ttl):
                    return Held(active)
                took = await runs_repo.update_held(
                    conn, active["id"], active["lease_holder"], lease_holder=holder, heartbeat_at=clock.now()
                )
                if not took:
                    return Held(active)
                log.warning("run %s: took over stale lease from %s", active["id"], active["lease_holder"])
                await lease_takeover_counted(conn)
                await outbox.telegram_message(
                    conn,
                    messages.run_resumed(active["id"]),
                    partition_key=outbox.run_partition(active["id"]),
                    idempotency_key=idempotency.outbox("run", active["id"], "resumed", request_key),
                )
                return Acquired(active["id"], active["card_cap"], resumed=True)
            run = await runs_repo.create(conn, holder=holder, card_cap=card_cap)
            await outbox.telegram_message(
                conn,
                messages.run_started(run["id"], card_cap),
                partition_key=outbox.run_partition(run["id"]),
                idempotency_key=idempotency.outbox("run", run["id"], "started"),
            )
            return Acquired(run["id"], card_cap, resumed=False)
    except IntegrityError:
        # Another request created a run between our read and insert: the unique index refused ours.
        async with transaction() as conn:
            active = await runs_repo.get_active(conn)
        if active is None:
            raise
        return Held(active)


async def heartbeat(holder: str) -> int:
    """Refresh this holder's run heartbeat; return how many runs it holds."""
    async with transaction() as conn:
        return await runs_repo.heartbeat(conn, holder)


async def lease_takeover_counted(conn: object) -> None:
    """System-health metric: how often a stale run lease was taken over (crashed worker)."""
    from jobpilot.db.queries import flags

    counters = dict(await flags.get(conn, "health_counters") or {})  # type: ignore[arg-type]
    counters["lease_takeovers"] = int(counters.get("lease_takeovers", 0)) + 1
    await flags.put(conn, "health_counters", counters)  # type: ignore[arg-type]
