"""Alerts to the owner's Telegram, written to the outbox (deduplicated per key per day).

Triggers: 80% of the daily token budget, 80% of the daily jobs-API quota, 3+ consecutive failures
of one integration, a run with no progress for 10 minutes or an outbox that is not draining, a dead outbox
row, an approved email not sent within 24 h, a run costing more than 1.5x the estimate, and Gmail token
refresh failing. Each alert has its own outbox partition, so a stuck alert never blocks anything else.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core import clock
from jobpilot.core.config import get_config
from jobpilot.db.queries import flags
from jobpilot.queue import outbox
from jobpilot.safety import idempotency

INTEGRATION_FAILURES = "integration_failures"


async def send(conn: AsyncConnection, key: str, text: str) -> None:
    """Queue an alert to the owner's Telegram, at most once per key per day.

    Args:
        conn: An open transaction.
        key: What the alert is about; repeats on the same day are
            dropped.
        text: The alert text.
    """
    day = clock.now().date().isoformat()
    await outbox.telegram_message(
        conn,
        f"⚠️ {text}",
        partition_key=f"alert:{key}:{day}",
        idempotency_key=idempotency.outbox("alert", key, day),
    )


async def integration_failed(conn: AsyncConnection, integration: str, error: str) -> None:
    """Count consecutive failures per integration; alert once the configured streak is reached."""
    counters: dict[str, Any] = dict(await flags.get(conn, INTEGRATION_FAILURES) or {})
    streak = int(counters.get(integration, 0)) + 1
    counters[integration] = streak
    await flags.put(conn, INTEGRATION_FAILURES, counters)
    if streak == get_config().alerts.integration_failures:
        await send(
            conn, f"integration:{integration}", f"{integration} failed {streak} times in a row: {error[:200]}"
        )
    if "token refresh failed" in error.lower():
        await send(
            conn, "gmail-auth", "Gmail token refresh is failing - run `make gmail-auth` on the laptop."
        )


async def integration_ok(conn: AsyncConnection, integration: str) -> None:
    """Reset an integration's failure streak after a success."""
    counters: dict[str, Any] = dict(await flags.get(conn, INTEGRATION_FAILURES) or {})
    if counters.get(integration):
        counters[integration] = 0
        await flags.put(conn, INTEGRATION_FAILURES, counters)


def budget_problem(used: int, budget: int | None) -> str | None:
    """Return an alert text when today's token use passes the alert fraction."""
    if budget and used >= get_config().alerts.budget_fraction * budget:
        return f"{used:,} of today's {budget:,} token budget used ({used / budget:.0%})."
    return None


def quota_problem(calls: int, cap: int) -> str | None:
    """Return an alert text when today's API calls pass the alert fraction."""
    if cap > 0 and calls >= get_config().alerts.quota_fraction * cap:
        return f"{calls} of today's {cap} jobs-API calls used ({calls / cap:.0%})."
    return None


def cost_problem(run_tokens: int) -> str | None:
    """Return an alert text when a run used more tokens than the estimate allows."""
    budgets = get_config().budgets
    limit = budgets.run_tokens_estimate * budgets.cost_alert_multiplier
    if run_tokens > limit:
        return (
            f"This run used {run_tokens:,} tokens, more than {budgets.cost_alert_multiplier:g}x the "
            f"estimate ({budgets.run_tokens_estimate:,})."
        )
    return None
