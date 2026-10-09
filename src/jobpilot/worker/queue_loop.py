"""Pulls work from the Postgres queue (SKIP LOCKED) and runs each item in its own task.

Woken by NOTIFY on 'work_items'; also polls. Handlers: Telegram events (commands, taps, chat) and
run requests. Taps keep working while a run is in progress because items run concurrently.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import psycopg
from sqlalchemy.exc import SQLAlchemyError

from jobpilot.core.config import get_config, get_settings
from jobpilot.db.session import transaction
from jobpilot.queue import jobs_queue

log = logging.getLogger(__name__)
Handler = Callable[[dict[str, Any]], Awaitable[None]]


class QueueLoop:
    """Claims work items and runs each one concurrently, up to the slot limit."""

    def __init__(self, holder: str, handlers: dict[str, Handler]) -> None:
        """Create the loop.

        Args:
            holder: This worker's lease holder id.
            handlers: Work kind -> async handler.
        """
        self.holder = holder
        self.handlers = handlers
        cfg = get_config()
        self.slots = asyncio.Semaphore(cfg.worker.concurrency)
        self.tasks: set[asyncio.Task[None]] = set()

    async def run_once(self) -> int:
        """Claim as many items as there are free slots and start them. Returns how many were started."""
        cfg = get_config()
        free = cfg.worker.concurrency - len(self.tasks)
        if free <= 0:
            return 0
        async with transaction() as conn:
            items = await jobs_queue.claim(
                conn, holder=self.holder, limit=free, ttl_seconds=cfg.lease.work_item_ttl_seconds
            )
        for item in items:
            task = asyncio.create_task(self._handle(dict(item)))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        return len(items)

    async def _handle(self, item: dict[str, Any]) -> None:
        """Run one work item, then mark it done or failed (with retry)."""
        cfg = get_config()
        async with self.slots:
            handler = self.handlers.get(item["kind"])
            try:
                if handler is None:
                    raise RuntimeError(f"no handler for work kind {item['kind']!r}")
                await handler(item["payload"])
            except Exception as exc:
                log.exception("work item %s (%s) failed", item["id"], item["kind"])
                async with transaction() as conn:
                    await jobs_queue.fail(
                        conn,
                        item["id"],
                        self.holder,
                        error=f"{type(exc).__name__}: {exc}",
                        attempts=item["attempts"],
                        max_attempts=cfg.worker.work_item_max_attempts,
                    )
                return
            async with transaction() as conn:
                await jobs_queue.finish(conn, item["id"], self.holder)

    async def drain(self) -> None:
        """Wait for every running item to finish (tests and shutdown)."""
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _claim_loop(self, wait: Callable[[float], Awaitable[None]]) -> None:
        """Claim and start work forever, waiting for a wake-up when idle.

        Args:
            wait: Waits up to N seconds for a NOTIFY.
        """
        poll = get_config().worker.poll_seconds
        while True:
            try:
                started = await self.run_once()
            except (psycopg.OperationalError, OSError, SQLAlchemyError):
                log.warning("claiming work failed (database unreachable?); retrying", exc_info=True)
                started = 0
                await asyncio.sleep(poll)
            if not started:
                await wait(poll)

    async def run_forever(self) -> None:
        """Claim work, woken by NOTIFY. The LISTEN connection is re-opened whenever the database (or the
        Supabase pooler) drops it; while it is down the loop simply polls - it never takes the worker down."""
        backoff = 1.0
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                    get_settings().psycopg_dsn, autocommit=True
                ) as listen:
                    await listen.execute(f"LISTEN {jobs_queue.CHANNEL}")
                    backoff = 1.0

                    async def wait(seconds: float) -> None:
                        """Wait for one NOTIFY, or until ``seconds`` pass."""
                        async for _ in listen.notifies(timeout=seconds, stop_after=1):
                            pass

                    await self._claim_loop(wait)
            except asyncio.CancelledError:
                raise
            except (psycopg.OperationalError, OSError) as exc:
                log.warning(
                    "work-queue LISTEN connection lost (%s); polling, reconnecting in %.0fs", exc, backoff
                )
                deadline = asyncio.get_running_loop().time() + backoff
                while asyncio.get_running_loop().time() < deadline:
                    try:
                        await self.run_once()
                    except (psycopg.OperationalError, OSError, SQLAlchemyError):
                        pass
                    await asyncio.sleep(min(get_config().worker.poll_seconds, backoff))
                backoff = min(backoff * 2, 60)
