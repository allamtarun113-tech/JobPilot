"""The Q&A bank: interview questions and answers in Qdrant, reused across jobs.

Tags: skill_ids (taxonomy ids), project_ids (profile items the answer
rests on), category (technical, system_design, behavioral, company,
dsa), source ("skill_note:<skill>" or "prep:<job id>"), and the job's
company and title for company questions. Same source + same question
-> same point, so re-saving is safe.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from jobpilot.core import clock
from jobpilot.integrations.vectors import QA_BANK, VectorStore
from jobpilot.llm.embeddings import Embedder
from jobpilot.models.prep import NoteQA, PackQuestion, Question
from jobpilot.reference import catalog

_PROFILE = re.compile(r"^profile:(\d+)$")


def point_id(source: str, question: str) -> int:
    """Return a stable point id for a question within a source."""
    key = f"{source}|{' '.join(question.lower().split())}".encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big") & ((1 << 63) - 1)


async def add(vectors: VectorStore, embedder: Embedder, items: list[dict[str, Any]]) -> int:
    """Save Q&A items (each needs question, answer, source; tags optional)."""
    items = [i for i in items if i.get("question") and i.get("answer")]
    if not items:
        return 0
    embedded = await embedder.embed([f"{i['question']}\n{i['answer'][:1500]}" for i in items])
    now = clock.now().isoformat()
    points = [
        (
            point_id(item["source"], item["question"]),
            vector,
            {
                "skill_ids": [],
                "project_ids": [],
                "category": "technical",
                "citations": [],
                "verified": False,
                "saved_at": now,
                **item,
            },
        )
        for item, vector in zip(items, embedded, strict=True)
    ]
    await vectors.upsert(QA_BANK, points)
    return len(points)


def from_skill_note(
    skill_id: str, qa: list[NoteQA], valid_citations: set[str], labels: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Turn a skill note's Q&A into bank entries.

    Args:
        skill_id: The skill the note is about.
        qa: The note's questions and answers.
        valid_citations: Source ids that really were given to the model.
        labels: Source id -> readable label ("Book, p. 12").

    Returns:
        Bank entries; only valid citations are kept.
    """
    out = []
    for item in qa:
        cited = [c for c in item.citations if c in valid_citations]
        out.append(
            {
                "source_labels": [(labels or {})[c] for c in cited if c in (labels or {})],
                "group": "skill",
                "question": item.question,
                "answer": item.answer,
                "category": "technical",
                "skill_ids": [skill_id],
                "citations": cited,
                "verified": bool(cited),
                "source": f"skill_note:{skill_id}",
            }
        )
    return out


def from_prep_set(job: dict[str, Any], questions: list[Question]) -> list[dict[str, Any]]:
    """A job's generated prep questions, tagged for reuse (skills from the topic and question text)."""
    out = []
    for q in questions:
        # Tag skills named by the topic, the pattern or words in the
        # question.
        skills = {sid for text in (q.topic, q.pattern or "") if text and (sid := catalog.skill_id(text))}
        skills |= {
            sid
            for word in re.findall(r"[A-Za-z][\w+#.-]{1,30}", q.question)
            if (sid := catalog.skill_id(word))
        }
        out.append(
            {
                "question": q.question,
                "answer": q.answer,
                "category": q.category,
                "topic": q.topic,
                "skill_ids": sorted(skills),
                "project_ids": [int(m.group(1)) for c in q.citations if (m := _PROFILE.match(c))],
                "citations": list(q.citations),
                "verified": q.verified,
                "source": f"prep:{job.get('id')}",
                "company": job.get("company"),
                "title": job.get("title"),
            }
        )
    return out


def group_source(group: str, about: str, job_id: int) -> str:
    """Where a prep v2 group's Q&A is filed, so the next pack finds it: per skill, per project, once for
    your experience and the HR round (shared by every job), and per job."""
    if group == "skill":
        return f"skill_qa:{catalog.skill_id(about) or about.lower()}"
    if group == "project":
        return f"project:{about.removeprefix('profile:')}"
    if group in ("experience", "hr"):
        return group
    return f"prep:{job_id}"


def from_pack(
    items: list[PackQuestion], job: dict[str, Any], labels: dict[str, str], valid: set[str]
) -> list[dict[str, Any]]:
    """Generated pack Q&A as bank entries, with their checked citations and readable source labels."""
    out = []
    for q in items:
        cited = [c for c in q.citations if c in valid]
        sid = catalog.skill_id(q.about) if q.group == "skill" else None
        project = _PROFILE.match(q.about) if q.group == "project" else None
        out.append(
            {
                "question": q.question,
                "answer": q.answer,
                "category": q.category,
                "group": q.group,
                "topic": q.about,
                "why_asked": q.why_asked,
                "skill_ids": [sid] if sid else [],
                "project_ids": [int(project.group(1))] if project else [],
                "citations": cited,
                "source_labels": [labels[c] for c in cited if c in labels],
                "verified": bool(cited),
                "source": group_source(q.group, q.about, int(job["id"])),
                "company": job.get("company"),
                "title": job.get("title"),
            }
        )
    return out


async def search(
    vectors: VectorStore,
    embedder: Embedder,
    text: str,
    *,
    limit: int = 10,
    must: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return bank entries closest in meaning to ``text`` (with their scores)."""
    [vector] = await embedder.embed([text])
    return [
        {**payload, "score": score}
        for _id, score, payload in await vectors.search(QA_BANK, vector, limit, must)
    ]
