"""Prep v2 (decision 34): the Prepare pack from storage first.

The pack builds from storage first, writes only the missing Q&A groups
once, fills every group, shows a source for every answer, and Known /
Weak marks still shape the study plan.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import update

from jobpilot.db.queries import cards as cards_repo
from jobpilot.db.queries import prep_sets as prep_repo
from jobpilot.db.queries import resume_versions as versions_repo
from jobpilot.db.session import transaction
from jobpilot.db.tables import scores
from jobpilot.integrations.vectors import PREP_CHUNKS
from jobpilot.models.prep import Concept, PackQuestion, PackQuestions, SkillNote
from jobpilot.prep import marks, service
from jobpilot.profile import confirm
from tests.integration.test_card_transitions import setup_card

pytestmark = pytest.mark.usefixtures("db")


class PrepLLM:
    """Writes a skill note, and Q&A for whichever groups the pack asks for."""

    def __init__(self) -> None:
        """Start with no recorded tasks."""
        self.tasks: list[str] = []

    async def structured(self, task: str, prompt: Any, output: type, **_kw: Any) -> Any:
        """Return a skill note, or Q&A for every group the prompt asks for."""
        self.tasks.append(task)
        if output is SkillNote:
            note = SkillNote(
                summary="SQL asks a database for data.",
                how_it_works="You describe the rows you want.",
                key_concepts=[Concept(name="window functions", explanation="compute across related rows")],
                when_to_use="Reports and analysis.",
                example="SELECT 1",
                citations=["chunk:700"],
            )
            return SimpleNamespace(output=note, model="m")
        wanted = prompt.user
        items = [
            PackQuestion(
                group="skill",
                about="SQL",
                category="technical",
                question="Explain a window function.",
                answer="It computes across related rows without collapsing them.",
                citations=["chunk:700"],
            ),
            PackQuestion(
                group="job",
                category="company",
                question="Why this retail team?",
                answer="Because the role turns sales data into decisions.",
                citations=[],
            ),
            PackQuestion(
                group="experience",
                category="behavioral",
                question="Tell me about your last role.",
                answer="I built reports.",
                citations=["profile:1"],
            ),
            PackQuestion(
                group="hr",
                category="behavioral",
                question="Tell me about yourself.",
                answer="I am an analyst.",
                citations=["profile:1"],
            ),
        ]
        items += [
            PackQuestion(
                group="project",
                about=about,
                category="technical",
                question=f"Walk me through {about}.",
                answer="I designed it end to end.",
                citations=[about],
            )
            for about in __import__("re").findall(r"profile:\d+", wanted.split("Job:")[0])
        ]
        return SimpleNamespace(output=PackQuestions(questions=items), model="m")


async def approved_card(p2_deps: Any) -> int:
    """Create a card with an approved resume, an SQL match and one SQL book section.

    Returns:
        The job id.
    """
    card_id, _ = await setup_card()
    async with transaction() as conn:  # the test database already holds the sample resume
        await confirm.confirm_all(conn)
    async with transaction() as conn:
        card = await cards_repo.get(conn, card_id)
        assert card is not None
        resume = await versions_repo.latest(conn, card["job_id"])
        assert resume is not None
        await cards_repo.update(
            conn, card_id, card["version"], status="resume_approved", approved_resume_version_id=resume["id"]
        )
        await conn.execute(  # you have SQL: the recap covers it
            update(scores)
            .where(scores.c.job_id == card["job_id"])
            .values(
                matched=[
                    {"skill": "SQL", "skill_id": "sql", "kind": "technology", "evidence": [1], "how": "id"}
                ]
            )
        )
    [vector] = await p2_deps.embedder.embed(
        ["SQL explained: concepts, how it works, examples window functions"]
    )
    await p2_deps.vectors.upsert(
        PREP_CHUNKS,
        [
            (
                700,
                vector,
                {
                    "source": "SQL Book",
                    "topic": "Window functions",
                    "page_start": 12,
                    "page_end": 14,
                    "skill_ids": ["sql"],
                    "text": "Window functions compute over partitions.",
                },
            )
        ],
    )
    return int(card["job_id"])


async def test_the_first_pack_fills_every_group_and_the_next_one_comes_from_storage(p2_deps: Any) -> None:
    """The first pack makes two model calls; the second makes none."""
    job_id = await approved_card(p2_deps)
    llm = PrepLLM()
    p2_deps.llm = llm
    await service.generate(job_id, p2_deps)
    async with transaction() as conn:
        prep = await prep_repo.get(conn, job_id)
    assert prep is not None and prep["status"] == "ready"
    groups = {q["group"] for q in prep["questions"]}
    assert {"skill", "job", "experience", "hr", "project"} <= groups  # every group filled
    assert all(q["source_labels"] for q in prep["questions"])  # every answer shows a source
    sql = next(q for q in prep["questions"] if q["group"] == "skill")
    assert sql["verified"] and sql["source_labels"] == ["SQL Book, p. 12"]
    unsourced = next(q for q in prep["questions"] if q["group"] == "job")
    assert not unsourced["verified"] and "check this answer yourself" in unsourced["source_labels"][0]
    pack = prep["pack"]
    assert [r["name"] for r in pack["recap"]] == ["SQL"] and pack["recap"][0]["new"]
    assert llm.tasks == ["skill_note", "qa_generator"]

    llm.tasks.clear()
    await service.generate(job_id, p2_deps)  # again: everything is stored now
    async with transaction() as conn:
        again = await prep_repo.get(conn, job_id)
    assert again is not None and llm.tasks == []  # no model call at all
    assert again["pack"]["stats"]["ai_calls"] == 0 and again["pack"]["stats"]["from_storage"] > 0
    assert {q["group"] for q in again["questions"]} >= {"skill", "job", "experience", "hr", "project"}


async def test_weak_answers_still_feed_the_study_plan(p2_deps: Any) -> None:
    """Marking an answer weak puts its topic on the study plan."""
    job_id = await approved_card(p2_deps)
    p2_deps.llm = PrepLLM()
    await service.generate(job_id, p2_deps)
    async with transaction() as conn:
        prep = await prep_repo.get(conn, job_id)
    assert prep is not None
    sql = next(q for q in prep["questions"] if q["group"] == "skill")
    await marks.mark(job_id, sql["id"], False, prep["version"])
    async with transaction() as conn:
        marked = await prep_repo.get(conn, job_id)
    assert marked is not None and marked["study_plan"]["weak_topics"] == ["SQL"]
