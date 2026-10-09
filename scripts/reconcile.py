"""Restore procedure step 2: reconcile pending side effects before the dispatcher resumes.

Run with the dispatcher paused (`make dispatcher-pause`, or start it with DISPATCHER_START_PAUSED=1).
- Pending / dead email sends: Gmail Sent is searched (deterministic Message-ID) and the draft is checked.
  Found in Sent -> recorded as sent (sent_emails, card emailed), never resent. Draft gone but nothing in Sent
  -> left for you to decide (maybe sent - Gmail search lags; or deleted). Otherwise left pending.
- Pending Telegram messages created before the backup time (--before ISO time): a bot can't read its own
  sent messages, so they are listed; `--skip-telegram` marks them skipped instead of re-sending them.
Nothing is ever resent by this script.

Usage: uv run python scripts/reconcile.py [--before 2026-10-05T10:00:00+05:30] [--skip-telegram] [--apply]
Without --apply it only reports.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

from sqlalchemy import select

from jobpilot.db.queries import approvals, cards, emails
from jobpilot.db.queries import outbox as outbox_repo
from jobpilot.db.session import dispose_engine, transaction
from jobpilot.db.tables import outbox
from jobpilot.integrations.mcp_client import MCPConnection
from jobpilot.integrations.mcp_process import ManagedClient
from jobpilot.models.card import CardStatus


async def main(args: argparse.Namespace) -> None:
    """Reconcile pending email sends and old Telegram messages after a restore.

    Only reports unless --apply is given; never sends anything.
    """
    before = datetime.fromisoformat(args.before) if args.before else None
    async with transaction() as conn:
        rows = list(
            (
                await conn.execute(
                    select(outbox).where(outbox.c.status.in_(("pending", "dead"))).order_by(outbox.c.id)
                )
            )
            .mappings()
            .all()
        )
    sends = [r for r in rows if r["kind"] == "gmail" and r["payload"].get("op") == "send_draft"]
    telegram = [r for r in rows if r["kind"] == "telegram" and (before is None or r["created_at"] < before)]
    print(f"{len(sends)} email send(s) and {len(telegram)} Telegram message(s) to reconcile")
    if sends:
        async with ManagedClient("gmail", "dispatcher") as client:
            gmail = MCPConnection("gmail", "dispatcher", client)
            for row in sends:
                p = row["payload"]
                # Ask Gmail whether this job's email is already in Sent.
                found = await gmail.call(
                    "find_sent",
                    {"job_id": p["job_id"], "draft_id": p.get("draft_id"), "thread_id": p.get("thread_id")},
                )
                message = (found.get("message") or {}) if found.get("ok") else {}
                if not found.get("ok"):
                    print(f"  outbox {row['id']}: Gmail check failed ({found.get('error')}); left as is")
                elif message.get("message_id"):
                    print(f"  outbox {row['id']}: ALREADY SENT (job {p['job_id']}); recording it")
                    if args.apply:
                        async with transaction() as conn:
                            await emails.record_sent(conn, p["job_id"], p["email_id"], message["message_id"])
                            email = await emails.get(conn, p["email_id"])
                            if email is not None:
                                await emails.set_status(conn, email["id"], "sent", email["lock_version"])
                            await approvals.mark_consumed(conn, p["approval_id"])
                            card = await cards.for_job(conn, p["job_id"])
                            if card is not None and card["status"] != CardStatus.EMAILED.value:
                                await cards.update(
                                    conn, card["id"], card["version"], status=CardStatus.EMAILED.value
                                )
                            delivered = await outbox_repo.mark_delivered(
                                conn, row["id"], message["message_id"]
                            )
                            if not delivered:  # a dead row: close it without delivering anything
                                await outbox_repo.mark_skipped(conn, row["id"], "reconciled: already sent")
                elif message.get("draft_exists") is False:
                    print(
                        f"  outbox {row['id']}: draft gone, not in Sent - check Gmail by hand before anything"
                    )
                else:
                    print(f"  outbox {row['id']}: not sent yet; safe to deliver after resume")
    for row in telegram:
        print(f"  telegram outbox {row['id']} ({row['partition_key']}, {row['payload'].get('op')})")
        if args.apply and args.skip_telegram:
            async with transaction() as conn:
                await outbox_repo.mark_skipped(conn, row["id"], "reconcile: created before the backup")
    if not args.apply:
        print("Report only. Re-run with --apply to record what was found.")
    print("When satisfied: make dispatcher-resume")
    await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--before")
    parser.add_argument("--skip-telegram", action="store_true")
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args()))
