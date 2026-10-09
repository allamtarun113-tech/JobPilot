"""Prep v2 (decision 34): build a job's interview pack mostly from storage.

The worker wires the Prep Research agent in as the `research` function, so this feature never imports the
agent or the run. For one job, from its requirements, missing skills, approved resume and confirmed profile:

1. Skill recap: for each job skill you have, its stored skill note (knowledge base). A missing note is written
   by one model call, at most MAX_NEW_NOTES per pack.
2. Resources (no model call): for the skills your tailored resume added, and for missing skills worth learning
   (ranked by points and learnability), from the learning-resources collection.
3. Interview Q&A in five groups - each job skill, each of your projects, the job, your experience, the HR
   round - read from the Q&A bank first. One model call writes only the groups still missing (grounded in book
   sections, your profile and web research), and they are saved to the bank for the next pack. Experience and
   HR answers are shared by every job.
Every answer shows its sources (a book page, a profile fact, a web page) or says it is unverified.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.db.queries import prep_sets as prep_repo
from jobpilot.db.queries import profile_items
from jobpilot.db.queries import requirements as req_repo
from jobpilot.db.queries import resume_versions as versions_repo
from jobpilot.db.queries import scores as scores_repo
from jobpilot.db.session import transaction
from jobpilot.integrations.vectors import QA_BANK, VectorStore
from jobpilot.knowledge import lookup
from jobpilot.llm.client import LLMClient
from jobpilot.llm.embeddings import Embedder
from jobpilot.models.prep import Question, ResearchNote, Topic
from jobpilot.models.profile import fact_texts
from jobpilot.prep import dsa_picker, qa_bank, qa_generator, retrieve, skill_notes, study_plan
from jobpilot.reference import catalog

log = logging.getLogger(__name__)
Research = Callable[[str, str, list[Topic]], Awaitable[list[ResearchNote]]]

MAX_SKILLS = 8  # job skills covered by one pack
MAX_NEW_NOTES = 4  # skill notes the model may write for one pack; the rest are written by later packs
PER_SKILL_QA = 2
SHARED_GROUPS = (("experience", "experience", 4), ("hr", "hr", 5))
CATEGORIES = {"technical", "system_design", "behavioral", "company", "dsa"}


class PrepDeps(Protocol):
    """What building a prep pack needs: the model, Qdrant and the embedder."""

    llm: LLMClient | None
    vectors: VectorStore
    embedder: Embedder


def job_skill_ids(requirements: dict[str, Any]) -> list[str]:
    """Return the taxonomy ids of every skill the job's requirements name."""
    names = [
        *requirements.get("technologies", []),
        *requirements.get("skills", []),
        *requirements.get("concepts", []),
        *requirements.get("nice_skills", []),
    ] or [*requirements.get("must_skills", []), *requirements.get("nice_skills", [])]
    return list(dict.fromkeys(sid for name in names if (sid := catalog.skill_id(str(name)))))


def _question(entry: dict[str, Any], *, from_storage: bool) -> Question:
    """Turn a bank entry into a Question for the page.

    An answer with no source is labelled so the user checks it.
    """
    labels = list(entry.get("source_labels") or [])
    if not labels:
        labels = ["no source found - check this answer yourself"]
    category = entry.get("category") if entry.get("category") in CATEGORIES else "technical"
    key = hashlib.sha256(f"{entry.get('group')}|{entry['question']}".encode()).hexdigest()[:10]
    return Question(
        id=f"{entry.get('group') or 'q'}-{key}",
        category=category,  # type: ignore[arg-type]
        topic=str(entry.get("topic") or entry.get("group") or ""),
        question=str(entry["question"]),
        answer=str(entry["answer"]),
        citations=list(entry.get("citations") or []),
        verified=bool(entry.get("verified")),
        why_asked=entry.get("why_asked") or None,
        source_labels=labels,
        group=entry.get("group") or "skill",
        from_storage=from_storage,
    )


async def _resources(vectors: VectorStore, skill_id: str | None, limit: int) -> list[dict[str, Any]]:
    """Return up to ``limit`` working learning resources for a skill."""
    if not skill_id:
        return []
    found = (await lookup.for_skill(vectors, skill_id, resources=limit, qa=0)).resources
    keys = ("title", "url", "provider", "kind", "level", "est_hours")
    return [{k: r.get(k) for k in keys} for r in found]


async def generate(job_id: int, deps: PrepDeps, researcher: Research | None = None) -> None:
    """Build a job's interview pack, mostly from storage.

    Args:
        job_id: The job (needs an approved resume).
        deps: The model, Qdrant and the embedder.
        researcher: The Prep Research agent, wired in by the worker.

    Raises:
        ValueError: No API key, or no approved resume yet.
    """
    if deps.llm is None:
        raise ValueError("Prepare needs the OpenAI API key")
    llm, vectors, embedder = deps.llm, deps.vectors, deps.embedder
    async with transaction() as conn:
        prep = await prep_repo.get(conn, job_id)
        card = await cards_repo.for_job(conn, job_id)
        job = await jobs_repo.get(conn, job_id)
        score = await scores_repo.latest_for(conn, job_id)
        approved = (
            await versions_repo.get(conn, card["approved_resume_version_id"])
            if card and card["approved_resume_version_id"]
            else None
        )
        items = await profile_items.list_all(conn, statuses=("confirmed",))
        req_row = await req_repo.get(conn, score["requirements_id"]) if score else None
        if prep is None:
            prep = await prep_repo.enqueue(conn, job_id)
        await prep_repo.update(conn, prep["id"], prep["version"], status="working", error=None)
    if card is None or job is None or score is None or approved is None:
        raise ValueError("approve a resume for this job before preparing")
    requirements = dict(req_row["data"]) if req_row else {}
    facts = fact_texts(items)
    gaps = list(score["gap"])
    jd_ids = job_skill_ids(requirements)
    matched = {m["skill_id"] for m in score["matched"] if m.get("skill_id")}
    have = [sid for sid in jd_ids if sid in matched][:MAX_SKILLS]
    added = [str(op["skill"]) for op in approved["operations"] or [] if op.get("op") == "add_skill"]
    stats = {"ai_calls": 0, "notes_written": 0, "from_storage": 0, "generated": 0}

    # 1. Skill recap: stored notes; a missing one is written once (and reused by every later pack).
    recap = []
    for sid in have:
        note = (await lookup.for_skill(vectors, sid, resources=0, qa=0)).note
        written = False
        if note is None and stats["notes_written"] < MAX_NEW_NOTES:
            try:
                await skill_notes.write(llm, vectors, embedder, sid)
                stats["ai_calls"] += 1
                stats["notes_written"] += 1
                written = True
                note = (await lookup.for_skill(vectors, sid, resources=0, qa=0)).note
            except Exception:
                log.warning("skill note for %s not written", sid, exc_info=True)
        if note:
            keys = ("name", "summary", "how_it_works", "key_concepts", "when_to_use", "pitfalls", "example",
                    "interview_points", "source_labels")  # fmt: skip
            recap.append({"skill_id": sid, "new": written, **{k: note.get(k) for k in keys}})

    # 2. Resources (no model call): skills your resume added, then missing skills worth learning.
    resources_added = [
        {"skill": name, "resources": await _resources(vectors, catalog.skill_id(name), 5)} for name in added
    ]
    worth = [g for g in gaps if str(g.get("skill")) not in added][:5]  # the gap list is ranked already
    resources_learn = [
        {
            "skill": g.get("skill"),
            "points": g.get("points"),
            "easy": g.get("easy"),
            "learn_reason": g.get("learn_reason"),
            "resources": await _resources(vectors, g.get("skill_id"), 3),
        }
        for g in worth
    ]

    # 3. Interview Q&A: the bank first, one model call for whatever group is still missing.
    questions: list[Question] = []
    wanted: dict[str, list[str]] = {}

    def reuse(rows: list[dict[str, Any]], group: str) -> None:
        """Add stored bank rows to the pack as questions from storage."""
        for row in rows:
            questions.append(_question({**row, "group": group}, from_storage=True))
        stats["from_storage"] += len(rows)

    for sid in jd_ids[:MAX_SKILLS]:
        stored = (await lookup.for_skill(vectors, sid, resources=0, qa=PER_SKILL_QA)).qa
        skill = catalog.get(sid)
        name = skill.name if skill else sid
        if stored:
            reuse([{**row, "topic": name} for row in stored], "skill")
        else:
            wanted.setdefault("skill", []).append(name)
    for item in items:
        if item["kind"] != "project":
            continue
        stored_rows = [p for _id, p in await vectors.scroll(QA_BANK, {"source": f"project:{item['id']}"}, 2)]
        if stored_rows:
            reuse(stored_rows, "project")
        else:
            wanted.setdefault("project", []).append(f"profile:{item['id']}")
    for group, source, limit in (*SHARED_GROUPS, ("job", f"prep:{job_id}", 6)):
        stored_rows = [p for _id, p in await vectors.scroll(QA_BANK, {"source": source}, limit)]
        if stored_rows:
            reuse(stored_rows, group)
        else:
            wanted[group] = [""]

    research: list[ResearchNote] = []
    if wanted:
        topics = [
            Topic(
                name=n[:120],
                category="technical",
                priority=1,
                skill_ids=[catalog.skill_id(n) or ""],
                reason="a skill this job asks for",
            )
            for n in wanted.get("skill", [])
        ]
        if "job" in wanted and researcher is not None:
            # Company interview research only when the job group is
            # missing.
            company_topic = Topic(name="interview process", category="company", priority=1, reason="company")
            try:
                research = await researcher(job["company"], job["title"], [*topics[:4], company_topic])
                stats["ai_calls"] += 1
            except Exception:
                log.warning("prep research for job %s failed", job_id, exc_info=True)
        sources = await retrieve.for_topics(vectors, embedder, topics, per_topic=2) if topics else []
        job_view = {"company": job["company"], "title": job["title"], "jd": str(job["jd_text"] or "")[:6000]}
        result = await qa_generator.generate_missing(
            llm,
            wanted=wanted,
            job=job_view,
            profile=facts,
            sources=sources,
            research=[note.model_dump(mode="json") for note in research],
        )
        stats["ai_calls"] += 1
        labels = {c["citation"]: f"{c.get('source') or 'book'}, p. {c.get('page_start')}" for c in sources}
        labels.update({f"profile:{i}": f"your profile: {text[:60]}" for i, text in facts.items()})
        labels.update({f"web:{n.source_url}": n.source_url for n in research})
        entries = qa_bank.from_pack(result.output.questions, dict(job), labels, set(labels))
        try:
            await qa_bank.add(vectors, embedder, entries)
        except Exception:
            log.warning("job %s: new prep Q&A not saved to the bank", job_id, exc_info=True)
        questions += [_question(entry, from_storage=False) for entry in entries]
        stats["generated"] = len(entries)

    order = {g: i for i, g in enumerate(("skill", "project", "job", "experience", "hr"))}
    questions.sort(key=lambda q: order.get(q.group or "", 9))
    as_dicts = [q.model_dump(mode="json") for q in questions]
    gap_ids = [str(gap.get("skill_id") or "") for gap in gaps]
    dsa = dsa_picker.pick([], gaps, str(job["jd_text"] or ""))
    async with transaction() as conn:
        current = await prep_repo.get(conn, job_id)
        assert current is not None
        await prep_repo.update(
            conn,
            current["id"],
            current["version"],
            status="ready",
            prompt_version="prep/qa_generator_v3" if stats["generated"] else None,
            model_id=None,
            topic_map=[],
            research=[note.model_dump(mode="json") for note in research],
            dsa=dsa,
            questions=as_dicts,
            pack={
                "recap": recap,
                "resources_added": resources_added,
                "resources_learn": resources_learn,
                "stats": stats,
            },
            study_plan=study_plan.build(as_dicts, gap_ids),
        )


async def fail(job_id: int, error: Exception) -> None:
    """Mark a job's prep set failed, with the error shown on the page."""
    async with transaction() as conn:
        prep = await prep_repo.get(conn, job_id)
        if prep is not None:
            await prep_repo.update(conn, prep["id"], prep["version"], status="failed", error=str(error)[:500])
