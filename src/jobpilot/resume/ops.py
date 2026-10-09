"""Apply checked structured operations to a copy of the base resume.

Every operation is validated on its own; anything that fails is dropped with a logged reason (never raised),
so one bad operation from the model can't fail a whole card. A rewrite must cite confirmed profile items,
every number must appear in them, and it may not introduce a skill or title word that neither the cited
items nor the original text contain. The one exception: missing job skills that code marked as
addable may be added to a skills line, and the summary may then mention them.
"""

from __future__ import annotations

import logging
from copy import deepcopy

from jobpilot.models.resume_doc import Layout, ResumeDoc
from jobpilot.models.resume_ops import (
    AddSkill,
    HideBullet,
    MoveEntry,
    ReorderSkills,
    ResumeOp,
    RewriteBullet,
    RewriteSummary,
)
from jobpilot.safety import claims
from jobpilot.safety.citations import supported_numbers

log = logging.getLogger(__name__)


def _check_rewrite(
    new_text: str,
    original: str,
    evidence_ids: list[int],
    confirmed_items: dict[int, str],
    extra: tuple[str, ...] = (),
) -> tuple[str | None, list[int]]:
    """Check one rewrite against the confirmed profile.

    A skill or title the confirmed profile backs is cited automatically
    (claims.backing_items); numbers must appear in the items the model
    itself cited.

    Args:
        new_text: The rewritten text.
        original: The text it replaces.
        evidence_ids: The item ids the model cited.
        confirmed_items: Confirmed item id -> fact text.
        extra: Added skills a summary may mention.

    Returns:
        (the reason to reject, or None; the citations to store).
    """
    evidence = {i: confirmed_items[i] for i in evidence_ids if i in confirmed_items}
    if set(evidence_ids) != set(evidence):
        return "unconfirmed evidence", evidence_ids
    if not supported_numbers(new_text, list(evidence.values())):
        return "number not present in cited profile items", evidence_ids
    allowed = [*evidence.values(), original, *extra]
    cited = list(dict.fromkeys(evidence_ids))
    for item_id in claims.backing_items(new_text, allowed, confirmed_items):
        if item_id not in cited:
            cited.append(item_id)
            allowed.append(confirmed_items[item_id])
    if problems := claims.unsupported(new_text, allowed):
        return "adds " + ", ".join(problems) + " not in your confirmed profile", evidence_ids
    return None, cited


def _key(item: str) -> str:
    """Return a skill's comparison key: lower case, single spaces."""
    return " ".join(item.lower().split())


def _reorder(current: list[str], proposed: list[str]) -> list[str] | None:
    """Return the line's own items in the proposed order.

    Items are matched ignoring case and spacing; items the proposal
    left out keep their relative order at the end, and anything it
    invented is ignored - a reorder never adds, drops or rewords a
    skill.

    Returns:
        The new order, or None when the proposal names none of the
        line's items.
    """
    by_key = {_key(item): item for item in current}
    ordered = list(dict.fromkeys(by_key[k] for p in proposed if (k := _key(p)) in by_key))
    if not ordered:
        return None
    return ordered + [item for item in current if item not in ordered]


def apply(
    base: ResumeDoc,
    operations: list[ResumeOp],
    confirmed_items: dict[int, str],
    addable: tuple[str, ...] = (),
) -> tuple[ResumeDoc, list[dict[str, object]], list[str]]:
    """Apply checked operations to a copy of the base resume.

    Args:
        base: The base resume (not changed).
        operations: The model's operations.
        confirmed_items: Confirmed item id -> fact text.
        addable: Job skills code allows adding to a skills line.

    Returns:
        (the tailored copy, the applied operations, the rejection
        reasons).
    """
    doc = deepcopy(base)
    applied: list[dict[str, object]] = []
    rejected: list[str] = []
    bullets = {b.id: b for s in doc.sections for e in s.entries for b in e.bullets}
    entries = {e.id: (s, e) for s in doc.sections for e in s.entries}
    # Reorders first (they name the original items), then skill additions, so a summary may mention them.
    ordered = sorted(
        operations, key=lambda o: 0 if isinstance(o, ReorderSkills) else 1 if isinstance(o, AddSkill) else 2
    )
    added: list[str] = []
    for op in ordered:
        reason = _apply_one(op, bullets, entries, confirmed_items, addable, tuple(added))
        if reason is None and isinstance(op, AddSkill):
            added.append(op.skill)
        if reason is None:
            applied.append(op.model_dump(mode="json"))
        else:
            target = getattr(op, "bullet_id", None) or getattr(op, "entry_id", "?")
            target = f"{target} ({op.skill})" if isinstance(op, AddSkill) else target
            rejected.append(f"{target}: {reason}")
    if rejected:
        log.info("resume operations rejected: %s", "; ".join(rejected))
    return doc, applied, rejected


def _apply_one(
    op: ResumeOp,
    bullets: dict,
    entries: dict,
    confirmed_items: dict[int, str],
    addable: tuple[str, ...] = (),
    added: tuple[str, ...] = (),
) -> str | None:
    """Apply one operation in place.

    Args:
        op: The operation.
        bullets: Bullet id -> bullet in the copy.
        entries: Entry id -> (section, entry) in the copy.
        confirmed_items: Confirmed item id -> fact text.
        addable: Job skills that may be added.
        added: Skills already added in this pass.

    Returns:
        None if applied, else the reason it was rejected.
    """
    if isinstance(op, RewriteBullet):
        bullet = bullets.get(op.bullet_id)
        if bullet is None:
            return "unknown bullet"
        reason, cited = _check_rewrite(op.text, bullet.text, op.evidence, confirmed_items)
        if reason:
            return reason
        op.evidence = cited
        bullet.text, bullet.evidence = op.text, cited
        return None
    if isinstance(op, HideBullet):
        bullet = bullets.get(op.bullet_id)
        if bullet is None:
            return "unknown bullet"
        bullet.hidden = True
        return None
    if isinstance(op, MoveEntry):
        found = entries.get(op.entry_id)
        if found is None:
            return "unknown entry"
        if op.before_entry_id == op.entry_id:
            return "an entry cannot move before itself"
        section, entry = found
        before = entries.get(op.before_entry_id) if op.before_entry_id else None
        if op.before_entry_id and before is None:
            return "unknown target entry"
        if before is not None and before[0].id != section.id:
            return "entries may only move within a section"
        section.entries.remove(entry)
        section.entries.insert(section.entries.index(before[1]) if before else 0, entry)
        return None
    if isinstance(op, RewriteSummary):
        found = entries.get(op.entry_id)
        if found is None or found[0].layout != Layout.PARAGRAPH:
            return "not a summary paragraph"
        entry = found[1]
        reason, cited = _check_rewrite(op.text, entry.text or "", op.evidence, confirmed_items, added)
        if reason:
            return reason
        op.evidence = cited
        entry.text, entry.evidence = op.text, cited
        return None
    if isinstance(op, AddSkill):
        found = entries.get(op.entry_id)
        if found is None or found[0].layout != Layout.SKILLS:
            return "not a skills line"
        if op.skill.lower() not in {s.lower() for s in addable}:
            return f"'{op.skill}' is not a job skill that may be added"
        if any(op.skill.lower() == s.lower() for _, e in entries.values() for s in e.items):
            return f"'{op.skill}' is already listed"
        found[1].items.append(op.skill)
        return None
    if isinstance(op, ReorderSkills):
        found = entries.get(op.entry_id)
        if found is None or found[0].layout != Layout.SKILLS:
            return "not a skills line"
        entry = found[1]
        reordered = _reorder(entry.items, op.items)
        if reordered is None:
            return "a reorder must name the line's own skills"
        op.items = reordered
        entry.items = reordered
        return None
    return "unknown operation"
