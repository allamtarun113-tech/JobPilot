"""Tier-2 Job Chat tools: proposals with Confirm / Cancel buttons.

They only create a Proposal (exact before / after). Nothing durable
changes until you tap Confirm, and the Confirm is version-checked
against what you saw.

- update_profile: edit an existing confirmed item's text, or add a new
  item ("I also know Docker").
- change_prefs: one preference field ("threshold 65 -> 70?").
- skip_snooze: skip the card, or snooze it until a date (it comes back
  in the reminder digest).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from jobpilot.cards import states
from jobpilot.core import clock
from jobpilot.core.config import get_config
from jobpilot.core.ids import short_id
from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import profile_items
from jobpilot.db.queries import proposals as proposals_repo
from jobpilot.db.session import transaction
from jobpilot.models.preferences import Preferences
from jobpilot.models.profile import ItemKind, ItemSource
from jobpilot.profile import preferences
from jobpilot.queue import outbox
from jobpilot.safety import idempotency

KINDS = {"update_profile", "change_prefs", "skip_snooze"}
MAX_SNOOZE_DAYS = 60


def _fmt(value: Any) -> str:
    """Format a preference value for the proposal text."""
    if value is None or value == []:
        return "not set"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _snooze_until(value: str) -> datetime:
    """Parse a snooze date into 9:00 local time that day, in UTC.

    Raises:
        ValueError: Not a date, or not within the next 60 days.
    """
    try:
        day = date.fromisoformat(value.strip()[:10])
    except ValueError as exc:
        raise ValueError("a snooze needs a date (YYYY-MM-DD)") from exc
    tz = get_config().tz
    until = datetime.combine(day, time(9, 0), tzinfo=tz).astimezone(UTC)
    if until <= clock.now() or until > clock.now() + timedelta(days=MAX_SNOOZE_DAYS):
        raise ValueError(f"a snooze date must be in the next {MAX_SNOOZE_DAYS} days")
    return until


async def create(job_id: int, kind: str, data: dict[str, Any]) -> int:
    """Create a proposal and send it with Confirm / Cancel buttons.

    The proposal records the target's version, so a Confirm after the
    target changed is refused.

    Args:
        job_id: The job the chat is about.
        kind: update_profile, change_prefs or skip_snooze.
        data: The model's proposal fields.

    Returns:
        The proposal id.

    Raises:
        ValueError: The proposal is invalid for this kind or card.
    """
    if kind not in KINDS:
        raise ValueError("unsupported proposal")
    async with transaction() as conn:
        card = await cards_repo.for_job(conn, job_id)
        if card is None:
            raise ValueError("job card not found")
        target_id = target_version = None
        if kind == "update_profile":
            text = str(data.get("text") or "").strip()
            if not text:
                raise ValueError("a profile change needs the exact text")
            if data.get("item_id"):
                item = await profile_items.get(conn, int(data["item_id"]))
                if item is None or item["status"] != "confirmed":
                    raise ValueError("profile edits must name an existing confirmed item")
                before, after = {"text": item["text"]}, {"text": text}
                target_id, target_version = item["id"], item["version"]
                summary = f"Change profile item:\nBefore: {item['text']}\nAfter: {text}"
            else:
                item_kind = str(data.get("kind") or "skill")
                if item_kind not in {k.value for k in ItemKind}:
                    raise ValueError(f"unknown profile item kind {item_kind!r}")
                before, after = {}, {"text": text, "kind": item_kind}
                summary = f"Add to your profile ({item_kind}):\n{text}"
        elif kind == "change_prefs":
            current, target_version = await preferences.load(conn)
            field, value = str(data.get("field", "")), str(data.get("value", ""))
            changed, change = preferences.apply_command(current, [field, value])
            before, after = current.model_dump(mode="json"), changed.model_dump(mode="json")
            summary = f"Change {change.field}: {_fmt(change.before)} -> {_fmt(change.after)}?"
        else:
            action = str(data.get("action") or "skip")
            if card["status"] not in states.NO_ACTION_STATES:
                raise ValueError(f"a {card['status'].replace('_', ' ')} card cannot be skipped or snoozed")
            if action == "snooze":
                until = _snooze_until(str(data.get("until") or data.get("value") or ""))
                before, after = {"snoozed_until": None}, {"snoozed_until": until.isoformat()}
                summary = f"Snooze this job until {until.astimezone(get_config().tz):%a %d %b}?"
            elif action == "skip":
                before, after = {"status": card["status"]}, {"status": "skipped"}
                summary = "Skip this job?"
            else:
                raise ValueError("propose skip or snooze")
            target_id, target_version = card["id"], card["version"]
        token = short_id()
        proposal_id = await proposals_repo.create(
            conn,
            job_id=job_id,
            kind=kind,
            target_id=target_id,
            target_version=target_version,
            before=before,
            after=after,
            callback_token=token,
        )
        await outbox.telegram_message(
            conn,
            f"Proposal - nothing changes unless you confirm.\n{summary}",
            partition_key=outbox.card_partition(card["id"]),
            idempotency_key=idempotency.outbox("proposal", proposal_id),
            reply_markup={
                "inline_keyboard": [
                    [
                        {"text": "Confirm", "callback_data": f"pc:{token}"},
                        {"text": "Cancel", "callback_data": f"px:{token}"},
                    ]
                ]
            },
        )
        return proposal_id


async def decide(token: str, *, confirm: bool) -> str:
    """Confirm or cancel a proposal from its button token.

    Args:
        token: The short id on the button.
        confirm: True for Confirm, False for Cancel.

    Returns:
        The reply to show.

    Raises:
        ValueError: Unknown proposal, or its target changed since.
    """
    async with transaction() as conn:
        proposal = await proposals_repo.by_token(conn, token)
        if proposal is None:
            raise ValueError("proposal not found")
        if proposal["status"] != "pending":
            return f"Proposal already {proposal['status']}."
        if not confirm:
            await proposals_repo.update_status(conn, proposal["id"], proposal["version"], "cancelled")
            return "Proposal cancelled."
        after = proposal["after"]
        if proposal["kind"] == "update_profile" and proposal["target_id"] is None:
            # A new item added from chat is the user's own, confirmed by
            # the tap.
            item_id = await profile_items.create(
                conn,
                kind=after["kind"],
                text=after["text"],
                evidence=f"added from Job Chat, confirmed by you (proposal {proposal['id']})",
                source=ItemSource.CHAT.value,
            )
            item = await profile_items.get(conn, item_id)
            assert item is not None
            await profile_items.set_status(conn, item_id, item["version"], "confirmed")
        elif proposal["kind"] == "update_profile":
            item = await profile_items.get(conn, proposal["target_id"])
            if item is None or item["version"] != proposal["target_version"]:
                raise ValueError("profile changed; create a fresh proposal")
            await profile_items.edit_text(conn, item["id"], item["version"], after["text"])
        elif proposal["kind"] == "change_prefs":
            _, version = await preferences.load(conn)
            if version != proposal["target_version"]:
                raise ValueError("preferences changed; create a fresh proposal")
            await preferences.save(conn, Preferences.model_validate(after), version)
        else:
            card = await cards_repo.get(conn, proposal["target_id"])
            if card is None or card["version"] != proposal["target_version"]:
                raise ValueError("card changed; create a fresh proposal")
            if "snoozed_until" in after:
                await cards_repo.update(
                    conn,
                    card["id"],
                    card["version"],
                    snoozed_until=datetime.fromisoformat(after["snoozed_until"]),
                    reminded_at=None,
                )
            else:
                states.require(card["status"], "skipped")
                await cards_repo.update(conn, card["id"], card["version"], status="skipped")
        await proposals_repo.update_status(conn, proposal["id"], proposal["version"], "confirmed")
        return "Proposal confirmed."
