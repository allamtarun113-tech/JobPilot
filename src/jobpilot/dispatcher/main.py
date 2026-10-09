"""Dispatcher process: delivers the outbox, in order per partition, through send-capable MCP connections.

- Exactly one dispatcher runs (a Postgres advisory lock held on its own connection).
- Woken by LISTEN / NOTIFY on 'outbox'; also wakes for scheduled retries.
- If the database drops the lock/LISTEN connection, the dispatcher reconnects and takes the lock again
  (another dispatcher may have taken it meanwhile - then this one waits).
- MCP server subprocesses are reopened if they die.
- Starts paused when DISPATCHER_START_PAUSED=1 (restore procedure: reconcile first, then resume).
"""

from __future__ import annotations

import asyncio
import logging
import os

import psycopg

from jobpilot.core import clock
from jobpilot.core.config import get_config, get_settings
from jobpilot.core.logging import setup_logging
from jobpilot.db.queries import flags
from jobpilot.db.session import dispose_engine, transaction
from jobpilot.dispatcher.delivery import Dispatcher
from jobpilot.integrations.gmail_sender import GmailSender
from jobpilot.integrations.mcp_client import MCPConnection
from jobpilot.integrations.mcp_process import ManagedClient
from jobpilot.integrations.telegram_sender import TelegramSender
from jobpilot.monitoring.tracing import setup_tracing, shutdown_tracing
from jobpilot.queue import outbox

log = logging.getLogger("dispatcher")
ADVISORY_LOCK_ID = 7_264_001  # "one dispatcher"


async def main() -> None:
    """Run the dispatcher process until it is stopped."""
    setup_logging("dispatcher")
    setup_tracing("dispatcher")
    try:
        await serve()
    finally:
        await dispose_engine()
        shutdown_tracing()


async def _start_paused_if_asked() -> None:
    """Turn on the paused flag when DISPATCHER_START_PAUSED=1 (after a restore)."""
    if os.environ.get("DISPATCHER_START_PAUSED") == "1":
        async with transaction() as conn:
            await flags.put(
                conn,
                flags.DISPATCHER_PAUSED,
                {"on": True, "reason": "start paused", "at": clock.now().isoformat()},
            )
        log.warning("dispatcher started PAUSED; run `make reconcile`, then `make dispatcher-resume`")


async def serve() -> None:
    """Open the send-capable MCP clients and deliver forever, reconnecting on errors."""
    cfg = get_config()
    await _start_paused_if_asked()
    if cfg.shadow_mode:
        log.info("shadow mode ON: emails are logged, not sent")
    async with (
        ManagedClient("telegram", "dispatcher") as tg_client,
        ManagedClient("gmail", "dispatcher") as gmail_client,
    ):
        dispatcher = Dispatcher(
            {
                "telegram": TelegramSender(MCPConnection("telegram", "dispatcher", tg_client)),
                "gmail": GmailSender(MCPConnection("gmail", "dispatcher", gmail_client)),
            },
            cfg.dispatcher,
            shadow_mode=cfg.shadow_mode,
        )
        backoff = 1.0
        while True:
            try:
                await _deliver_with_lock(dispatcher)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except (psycopg.OperationalError, OSError) as exc:
                log.warning("dispatcher database connection lost (%s); reconnecting in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)


async def _deliver_with_lock(dispatcher: Dispatcher) -> None:
    """Hold the one-dispatcher lock, then deliver whenever woken.

    Wakes on NOTIFY or when the next retry is due, whichever is
    sooner.
    """
    cfg = get_config()
    # A dedicated session connection: advisory locks and LISTEN don't
    # work through the transaction pooler.
    async with await psycopg.AsyncConnection.connect(
        get_settings().psycopg_dsn, autocommit=True
    ) as lock_conn:
        while True:
            got = await (
                await lock_conn.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_ID,))
            ).fetchone()
            if got and got[0]:
                break
            log.warning("another dispatcher holds the lock; waiting")
            await asyncio.sleep(30)
        await lock_conn.execute(f"LISTEN {outbox.CHANNEL}")
        log.info("dispatcher started (telegram %s)", "DRY-RUN" if get_settings().telegram_dry_run else "live")
        while True:
            try:
                await dispatcher.deliver_ready()
                wait = await dispatcher.seconds_until_next_due()
            except (psycopg.OperationalError, OSError):
                raise
            except Exception:
                log.exception("dispatcher cycle failed")
                wait = None
            timeout = (
                cfg.dispatcher.poll_seconds
                if wait is None
                else min(max(wait, 0.05), cfg.dispatcher.poll_seconds)
            )
            # Sleep until a NOTIFY arrives or the timeout passes.
            async for _ in lock_conn.notifies(timeout=timeout, stop_after=1):
                pass


if __name__ == "__main__":
    asyncio.run(main())
