"""Turns outbox rows into MCP calls.

- Each partition is delivered strictly in sequence order; a failing head row pauses its partition.
- Retries use exponential backoff; a non-retryable error (or too many attempts) marks the row dead and an
  alert is raised. Rows that can never succeed and must not block (edits of a deleted message, superseded
  sends) are skipped instead.
- Email sends are time-bounded by their approval (24 h), not by an attempt count: a Gmail outage or a
  daily cap defers them; when the approval expires the card becomes 'needs re-confirm' and you are asked.
- Edit targets are resolved from the stored message id at delivery time.
- Shadow mode logs emails instead of sending them (and, by default, does not create real Gmail drafts).
- No transaction is held during an MCP call: read -> deliver -> short write.

Only this module calls Telegram or Gmail tools that have side effects.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Protocol

from opentelemetry import trace
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.core import clock
from jobpilot.core.config import DispatcherCfg, get_config, get_settings
from jobpilot.core.errors import DeliveryError, DeliverySkipped
from jobpilot.db.queries import approvals as approvals_repo
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import emails as emails_repo
from jobpilot.db.queries import flags
from jobpilot.db.queries import outbox as repo
from jobpilot.db.session import read, transaction
from jobpilot.models.card import CardStatus
from jobpilot.monitoring import alerts
from jobpilot.queue import outbox, reconfirm
from jobpilot.safety import approval_tokens, approval_window, caps, idempotency, killswitch

log = logging.getLogger(__name__)
tracer = trace.get_tracer("jobpilot.dispatcher")
# How long a send waits before re-checking an engaged kill switch.
KILLSWITCH_RECHECK_SECONDS = 300


class Sender(Protocol):
    """Delivers one kind of outbox row (telegram or gmail)."""

    async def deliver(self, payload: dict[str, Any]) -> str | None:
        """Perform the side effect and return the external id (message id). Raise DeliveryError on failure."""
        ...


class DeliveryDeferred(DeliveryError):
    """Not now, not a failure: a daily cap or the kill switch. Retried at `retry_at` without an attempt."""

    def __init__(self, message: str, retry_at: datetime) -> None:
        """Create the deferral.

        Args:
            message: Why delivery waits.
            retry_at: When to try again.
        """
        super().__init__(message, retryable=True)
        self.retry_at = retry_at


def _is_email_send(row: RowMapping | dict[str, Any]) -> bool:
    """Return True if an outbox row is a Gmail send_draft."""
    return row["kind"] == "gmail" and (row["payload"] or {}).get("op") == "send_draft"


class Dispatcher:
    """Delivers outbox rows in order per partition, with retries and checks."""

    def __init__(self, senders: dict[str, Sender], cfg: DispatcherCfg, *, shadow_mode: bool) -> None:
        """Create the dispatcher.

        Args:
            senders: Kind (telegram, gmail) -> sender.
            cfg: Retry and backoff settings.
            shadow_mode: Log emails instead of sending them.
        """
        self.senders = senders
        self.cfg = cfg
        self.shadow_mode = shadow_mode

    async def is_paused(self) -> bool:
        """Return True while the dispatcher-paused flag is on (restore procedure)."""
        async with read() as conn:
            return await flags.is_on(conn, flags.DISPATCHER_PAUSED)

    async def deliver_ready(self, max_rows: int = 200) -> int:
        """Deliver ready head rows, partition by partition, until none is ready. Returns rows delivered."""
        delivered = 0
        while delivered < max_rows:
            if await self.is_paused():
                return delivered
            async with read() as conn:
                heads = await repo.partition_heads(conn)
            now = clock.now()
            ready = [h for h in heads if h["status"] == "pending" and h["next_attempt_at"] <= now]
            if not ready:
                return delivered
            progressed = False
            for row in ready:
                if await self._deliver_one(row):
                    delivered += 1
                    progressed = True
                else:
                    # A row skipped during delivery also unblocks its
                    # partition.
                    async with transaction() as conn:
                        current = await repo.get(conn, row["id"])
                    if current is not None and current["status"] == "skipped":
                        progressed = True
            if not progressed:
                return delivered
        return delivered

    async def _deliver_one(self, row: RowMapping) -> bool:
        """Deliver one head row and record the result.

        Returns:
            True if it was delivered.
        """
        with tracer.start_as_current_span(
            "outbox.deliver",
            attributes={
                "outbox.id": row["id"],
                "outbox.kind": row["kind"],
                "outbox.partition": row["partition_key"],
            },
        ) as span:
            try:
                payload = dict(row["payload"])
                external_id = None
                sender = self.senders.get(row["kind"])
                reconcile = getattr(sender, "reconcile", None)
                # Retry after a crash: check Gmail Sent before sending
                # again.
                if _is_email_send(row) and not self.shadow_mode and reconcile:
                    external_id = await reconcile(payload)
                if external_id is None:
                    payload = await self._resolve(row, payload)
                    external_id = await self._send(row, payload)
            except DeliverySkipped as exc:
                async with transaction() as conn:
                    await repo.mark_skipped(conn, row["id"], str(exc))
                return False
            except DeliveryDeferred as exc:
                async with transaction() as conn:
                    await repo.mark_failed(
                        conn, row["id"], error=str(exc), next_attempt_at=exc.retry_at, count_attempt=False
                    )
                return False
            except DeliveryError as exc:
                span.set_attribute("outbox.error", str(exc))
                await self._record_failure(row, exc)
                return False
            except Exception as exc:  # an unexpected bug: retry with backoff, keep the partition paused
                log.exception("outbox %s: unexpected delivery error", row["id"])
                await self._record_failure(row, DeliveryError(f"{type(exc).__name__}: {exc}"))
                return False
            async with transaction() as conn:
                await self._record_domain_success(conn, row, payload, external_id)
                ok = await repo.mark_delivered(conn, row["id"], external_id)
                if ok:
                    await alerts.integration_ok(conn, row["kind"])
            if not ok:
                log.warning("outbox %s was no longer pending when marking delivered", row["id"])
            return ok

    async def _resolve(self, row: RowMapping, payload: dict[str, Any]) -> dict[str, Any]:
        """Prepare a payload for delivery.

        Resolves an edit's target message id from the stored outbox row
        and, for an email send, checks the approval and mints the send
        token (outside shadow mode).

        Raises:
            DeliverySkipped: The edit target was never delivered.
            DeliveryError: The edit target has no message id yet.
        """
        target = payload.pop("target_outbox_id", None)
        if target is not None:
            async with transaction() as conn:
                target_row = await repo.get(conn, target)
            if target_row is not None and target_row["status"] == "skipped":
                raise DeliverySkipped(f"edit target outbox {target} was never delivered")
            if target_row is None or target_row["external_id"] is None:
                raise DeliveryError(f"edit target outbox {target} has no message id yet")
            payload["message_id"] = int(target_row["external_id"])
        if _is_email_send(row):
            await self._check_send(row, payload)
            if self.shadow_mode:
                return payload
            payload["approval_token"] = self._mint(payload)
        return payload

    @staticmethod
    def _mint(payload: dict[str, Any]) -> str:
        """Option B (Ed25519 signing key) when configured, otherwise the v1 HMAC key."""
        settings = get_settings()
        job_id, draft_id, version = int(payload["job_id"]), str(payload["draft_id"]), int(payload["version"])
        if settings.approval_signing_key and settings.approval_signing_key.get_secret_value():
            return approval_tokens.mint_signed(
                settings.approval_signing_key.get_secret_value(),
                job_id=job_id,
                draft_id=draft_id,
                version=version,
            )
        secret = settings.approval_token_key
        if secret is None or not secret.get_secret_value():
            raise DeliveryError("APPROVAL_TOKEN_KEY is not configured", retryable=False)
        return approval_tokens.mint(
            secret.get_secret_value().encode(), job_id=job_id, draft_id=draft_id, version=version
        )

    async def _check_send(self, row: RowMapping, payload: dict[str, Any]) -> None:
        """The exact approved draft, still current and still wanted; inside its 24-hour window; under the
        caps; kill switch off. Expiry moves the card to needs_reconfirm and asks for one fresh tap."""
        required = {"approval_id", "email_id", "job_id", "draft_id", "version"}
        if not required <= payload.keys():
            raise DeliverySkipped("unapproved or incomplete email send")
        expired_notice = False
        async with transaction() as conn:
            approval = await approvals_repo.get(conn, int(payload["approval_id"]))
            if approval is None or (
                approval["kind"],
                approval["job_id"],
                approval["target_id"],
                approval["target_version"],
            ) != ("email", payload["job_id"], payload["email_id"], payload["version"]):
                raise DeliveryError("email approval does not match the queued draft", retryable=False)
            card = await cards_repo.for_job(conn, int(payload["job_id"]))
            email = await emails_repo.get(conn, int(payload["email_id"]))
            latest = await emails_repo.latest(conn, int(payload["job_id"]))
            if (
                card is None
                or email is None
                or latest is None
                or latest["id"] != email["id"]
                or email["status"] != "sending"
                or card["status"] != CardStatus.SENDING.value
                or card["approved_resume_version_id"] != email["resume_version_id"]
                or email["gmail_draft_id"] != payload["draft_id"]
                or email.get("gmail_thread_id") != payload.get("thread_id")
                or email["version"] != payload["version"]
                or approval["consumed_at"] is not None
            ):
                raise DeliverySkipped("email or resume approval was superseded or skipped")
            if approval_window.expired(approval["approved_at"]):
                await reconfirm.expire_send(conn, card, email, approval)
                expired_notice = True
            elif await killswitch.is_engaged(conn):
                retry = clock.now() + timedelta(seconds=KILLSWITCH_RECHECK_SECONDS)
                raise DeliveryDeferred("kill switch engaged; sending paused", retry)
            else:
                try:
                    await caps.check_email_capacity(conn, int(payload["job_id"]), outbox_id=int(row["id"]))
                except caps.CapReached as exc:
                    if exc.retry_at is not None:
                        raise DeliveryDeferred(str(exc), exc.retry_at) from exc
                    raise DeliverySkipped(str(exc)) from exc
        if expired_notice:
            raise DeliverySkipped("email approval expired; asked for a fresh tap")

    async def _send(self, row: RowMapping, payload: dict[str, Any]) -> str | None:
        """Call the sender for a row, or log it in shadow mode.

        Returns:
            The external id (message or draft id).
        """
        kind = row["kind"]
        op = payload.get("op")
        if kind == "gmail" and self.shadow_mode and op == "send_draft":
            log.info("SHADOW MODE: email not sent (outbox %s)", row["id"])
            return f"shadow:{row['id']}"
        if kind == "gmail" and self.shadow_mode and op == "create_draft":
            if not get_config().shadow_creates_gmail_drafts:
                log.info("SHADOW MODE: Gmail draft not created (outbox %s)", row["id"])
                return f"shadow-draft:{row['id']}"
        sender = self.senders.get(kind)
        if sender is None:
            raise DeliveryError(f"no sender for kind {kind!r}", retryable=True)
        return await sender.deliver(payload)

    async def _record_domain_success(
        self,
        conn: AsyncConnection,
        row: RowMapping,
        payload: dict[str, Any],
        external_id: str | None,
    ) -> None:
        """Record what a delivery changed: card message ids, draft ids, sent emails."""
        if row["kind"] == "telegram":
            await self._record_card_message(conn, row, external_id)
            return
        if row["kind"] != "gmail" or external_id is None or "email_id" not in payload:
            return
        email_id = int(payload["email_id"])
        email = await emails_repo.get(conn, email_id)
        if email is None:
            return
        if payload.get("op") == "create_draft":
            # create_draft returns "draft_id|thread_id".
            draft_id, _, thread_id = external_id.partition("|")
            await emails_repo.set_draft_id(conn, email_id, draft_id, email["lock_version"], thread_id or None)
            return
        if payload.get("op") != "send_draft":
            return
        card = await cards_repo.for_job(conn, int(payload["job_id"]))
        await approvals_repo.mark_consumed(conn, int(payload["approval_id"]))
        if external_id.startswith("shadow:"):
            await emails_repo.set_status(conn, email_id, "shadowed", email["lock_version"])
            if card is not None:
                await cards_repo.update(conn, card["id"], card["version"], status=CardStatus.APPLIED.value)
            notice = f"SHADOW MODE: email v{email['version']} to {email['to_address']} was logged, not sent."
        else:
            await emails_repo.record_sent(conn, int(payload["job_id"]), email_id, external_id)
            await emails_repo.set_status(conn, email_id, "sent", email["lock_version"])
            if card is not None:
                await cards_repo.update(conn, card["id"], card["version"], status=CardStatus.EMAILED.value)
            notice = f"Email v{email['version']} sent to {email['to_address']}."
        await outbox.telegram_message(
            conn,
            notice,
            partition_key=f"notice:sent:{email_id}",
            idempotency_key=idempotency.outbox("email-result", email_id, row["id"]),
        )

    async def _record_card_message(
        self, conn: AsyncConnection, row: RowMapping, external_id: str | None
    ) -> None:
        """Store the Telegram message id of a card's own message."""
        if external_id is None or not str(row["partition_key"]).startswith("card:"):
            return
        card = await cards_repo.get(conn, int(str(row["partition_key"]).split(":", 1)[1]))
        if card is not None and card["telegram_outbox_id"] == row["id"] and external_id.isdigit():
            await cards_repo.set_telegram_message_id(conn, card["id"], int(external_id))

    async def _record_failure(self, row: RowMapping, exc: DeliveryError) -> None:
        """Schedule a retry with backoff, or mark the row dead and alert."""
        attempts = row["attempts"] + 1
        email_send = _is_email_send(row)
        # An email send is bounded by its approval window (24 h), not by an attempt count.
        give_up = not exc.retryable or (not email_send and attempts >= self.cfg.max_attempts)
        if give_up:
            log.error(
                "outbox %s dead after %s attempts: %s (partition %s paused)",
                row["id"],
                attempts,
                exc,
                row["partition_key"],
            )
            next_at = None
        else:
            delay = exc.retry_after or min(
                self.cfg.backoff_base_seconds * 2 ** (attempts - 1), self.cfg.backoff_max_seconds
            )
            if email_send:
                delay = max(delay, self.cfg.email_first_retry_seconds)  # let Gmail search index a send
            next_at = clock.now() + timedelta(seconds=delay)
            log.warning("outbox %s failed (attempt %s), retry in %.0fs: %s", row["id"], attempts, delay, exc)
        async with transaction() as conn:
            await repo.mark_failed(conn, row["id"], error=str(exc), next_attempt_at=next_at)
            await alerts.integration_failed(conn, row["kind"], str(exc))
            if next_at is None:
                await alerts.send(
                    conn,
                    f"dead:{row['id']}",
                    f"A {row['kind']} message could not be delivered and its queue ({row['partition_key']}) "
                    f"is paused: {str(exc)[:200]}. See docs/runbook.md (outbox recovery).",
                )

    async def seconds_until_next_due(self) -> float | None:
        """Return seconds until the next pending row is due, or None."""
        async with read() as conn:
            due = await repo.next_due_at(conn)
        if due is None:
            return None
        return max(0.0, (due - clock.now()).total_seconds())
