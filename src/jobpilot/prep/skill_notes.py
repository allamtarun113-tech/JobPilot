"""Skill notes: one deep, simple-English recap per skill, written once.

Written by one model call and reused. The call is grounded in the book
sections retrieved for the skill (cited by id; unknown ids are dropped)
and also writes 5-8 interview Q&A, which go into the Q&A bank. The note
is stored in Qdrant (``skill_notes``), one point per skill, so later
lookups need no model call (decision 28).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from jobpilot.core import clock
from jobpilot.integrations.vectors import PREP_CHUNKS, SKILL_NOTES, VectorStore
from jobpilot.llm import prompts
from jobpilot.llm.client import LLMClient
from jobpilot.llm.embeddings import Embedder
from jobpilot.models.prep import SkillNote
from jobpilot.prep import qa_bank
from jobpilot.reference import catalog
from jobpilot.safety import sanitize

SOURCES = 6


def point_id(skill_id: str) -> int:
    """Return the stable Qdrant point id of a skill's note."""
    return int.from_bytes(hashlib.sha256(f"note:{skill_id}".encode()).digest()[:8], "big") & ((1 << 63) - 1)


async def exists(vectors: VectorStore, skill_id: str) -> bool:
    """Return True if a note for this skill is already stored."""
    return bool(await vectors.scroll(SKILL_NOTES, {"skill_id": skill_id}, limit=1))


async def sources_for(vectors: VectorStore, embedder: Embedder, skill_id: str) -> list[dict[str, Any]]:
    """Book sections about the skill: tagged with it first, then the closest by meaning."""
    skill = catalog.get(skill_id)
    name = skill.name if skill else skill_id
    [vector] = await embedder.embed([f"{name} explained: concepts, how it works, examples"])
    tagged = await vectors.search(PREP_CHUNKS, vector, limit=SOURCES, must={"skill_ids": skill_id})
    closest = await vectors.search(PREP_CHUNKS, vector, limit=SOURCES)
    picked: dict[int, dict[str, Any]] = {}
    for pid, score, payload in [*tagged, *closest]:
        # Weak matches would only invite citations of unrelated pages.
        if pid not in picked and len(picked) < SOURCES and score >= 0.55:
            picked[pid] = {"id": f"chunk:{pid}", "score": round(score, 3), **payload}
    return list(picked.values())


async def write(
    llm: LLMClient, vectors: VectorStore, embedder: Embedder, skill_id: str
) -> tuple[SkillNote, int]:
    """Write and store the note for one skill. Returns the note and how many Q&A went to the bank."""
    skill = catalog.get(skill_id)
    if skill is None:
        raise ValueError(f"unknown skill id {skill_id!r}")
    sources = await sources_for(vectors, embedder, skill_id)
    related = [s.name for sid in (*skill.prerequisites, *skill.implies) if (s := catalog.get(sid))]
    rows = [
        {"id": s["id"], "book": s.get("source"), "page": s.get("page_start"), "topic": s.get("topic"),
         "text": str(s.get("text") or "")[:2500]}
        for s in sources
    ]  # fmt: skip
    prompt = prompts.render(
        "prep/skill_note",
        skill=skill.name,
        category=skill.category,
        related=", ".join(related) or "(none)",
        sources=sanitize.fence("sources", json.dumps(rows, ensure_ascii=True)),
    )
    result = await llm.structured("skill_note", prompt, SkillNote, max_tokens=6000)
    note = result.output
    valid = {s["id"] for s in sources}
    labels = {s["id"]: f"{s.get('source') or 'book'}, p. {s.get('page_start')}" for s in sources}
    # Keep only citations of sections the model was really given.
    cited = [c for c in note.citations if c in valid]
    [vector] = await embedder.embed([f"{skill.name}\n{note.summary}"])
    payload = {
        "skill_id": skill_id,
        "name": skill.name,
        **note.model_dump(mode="json", exclude={"citations", "qa"}),
        "citations": cited,
        "source_labels": [labels[c] for c in cited],
        "prompt": prompt.id,
        "model": getattr(result, "model", None),
        "written_at": clock.now().isoformat(),
    }
    await vectors.upsert(SKILL_NOTES, [(point_id(skill_id), vector, payload)])
    saved = await qa_bank.add(vectors, embedder, qa_bank.from_skill_note(skill_id, note.qa, valid, labels))
    return note, saved
