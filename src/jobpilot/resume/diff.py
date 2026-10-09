"""Human-readable structural differences between resume versions ('bullet b17 reworded, moved up')."""

from jobpilot.models.resume_doc import ResumeDoc


def describe(before: ResumeDoc, after: ResumeDoc) -> list[str]:
    """Describe how ``after`` differs from ``before``, one change per line.

    Args:
        before: The base resume.
        after: The tailored resume.

    Returns:
        Lines like "bullet b17 reworded" or "entries reordered in
        Projects (e3 moved up)".
    """
    changes: list[str] = []
    old_b = {b.id: b for s in before.sections for e in s.entries for b in e.bullets}
    new_b = {b.id: b for s in after.sections for e in s.entries for b in e.bullets}
    for bullet_id, old in old_b.items():
        new = new_b.get(bullet_id)
        if new is None:
            changes.append(f"bullet {bullet_id} removed")
            continue
        if old.text != new.text:
            changes.append(f"bullet {bullet_id} reworded")
        if not old.hidden and new.hidden:
            changes.append(f"bullet {bullet_id} hidden")
        if old.hidden and not new.hidden:
            changes.append(f"bullet {bullet_id} shown again")
    old_e = {e.id: e for s in before.sections for e in s.entries}
    for new_s in after.sections:
        for entry in new_s.entries:
            old_entry = old_e.get(entry.id)
            if old_entry is None:
                continue
            if (old_entry.text or "") != (entry.text or ""):
                changes.append(f"{new_s.title}: {entry.title or 'summary'} reworded")
            # Same items in a new order is a reorder, not an edit.
            if old_entry.items != entry.items and sorted(old_entry.items) == sorted(entry.items):
                changes.append(f"{new_s.title}: {entry.title or 'skills'} reordered")
            for item in entry.items:
                if item not in old_entry.items:
                    changes.append(f"added skill {item} ({entry.title or new_s.title})")
    old_sections = {s.id: s for s in before.sections}
    for new_s in after.sections:
        old_s = old_sections.get(new_s.id)
        if old_s is None:
            continue
        old_order = [e.id for e in old_s.entries]
        new_order = [e.id for e in new_s.entries]
        if old_order != new_order:
            # An entry moved up if it now sits before its old index.
            moved = [e for i, e in enumerate(new_order) if e in old_order and old_order.index(e) > i]
            label = f" ({', '.join(moved)} moved up)" if moved else ""
            changes.append(f"entries reordered in {new_s.title}{label}")
    return changes or ["No tailoring changes; base resume retained"]


def summary(changes: list[str]) -> str:
    """One line for the jobs table and the Telegram caption: 'added Docker, Kafka · 3 bullets reworded'."""
    added = [c.removeprefix("added skill ").split(" (")[0] for c in changes if c.startswith("added skill ")]
    counts = {
        "bullet reworded": sum(c.startswith("bullet ") and c.endswith("reworded") for c in changes),
        "bullet hidden": sum(c.endswith(" hidden") for c in changes),
        "summary reworded": sum(c.endswith("reworded") and not c.startswith("bullet ") for c in changes),
        "section reordered": sum("reordered" in c for c in changes),
    }
    parts = [f"added {', '.join(added)}"] if added else []
    for label, n in counts.items():
        if n:
            noun, verb = label.split(" ", 1) if " " in label else ("", label)
            parts.append(f"{n} {noun}{'s' if n > 1 and noun else ''} {verb}".replace("  ", " ").strip())
    return " · ".join(parts) or "no changes (original resume)"
