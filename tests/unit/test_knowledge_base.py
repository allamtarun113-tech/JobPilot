"""Phase 3: the knowledge base in Qdrant.

Learning resources, skill notes, the Q&A bank, the no-model skill
lookup and learnability.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from jobpilot.integrations.vectors import PREP_CHUNKS, PROFILE_ITEMS, QA_BANK, SKILL_NOTES, VectorStore
from jobpilot.knowledge import learning, lookup
from jobpilot.llm.embeddings import HashEmbedder
from jobpilot.matching import gap
from jobpilot.models.prep import Concept, NoteQA, Question, SkillNote
from jobpilot.models.score import GapItem
from jobpilot.prep import qa_bank, skill_notes
from jobpilot.resume.skill_additions import why_addable


@pytest.fixture
async def kb() -> Any:
    """Yield an in-memory Qdrant store and a hash embedder."""
    vectors = VectorStore.in_memory()
    yield vectors, HashEmbedder()
    await vectors.close()


async def test_learning_resources_keep_catalog_rank_and_hide_broken_links(kb: Any) -> None:
    """Lookups keep the catalog order, drop broken links and use catalog URLs."""
    vectors, embedder = kb
    assert await learning.ingest(vectors, embedder) == len(learning.all_resources())
    mapping, resources = learning.data()
    skill = next(s for s, ids in mapping.items() if len(ids) >= 3)
    found = await lookup.for_skill(vectors, skill)
    ranked = [rid for rid in mapping[skill] if learning.link_ok(resources[rid])]
    assert [r["resource_id"] for r in found.resources][: len(ranked)] == ranked[:20]
    assert all(r["url"] == resources[r["resource_id"]]["url"] for r in found.resources)  # catalog URLs only


class NoteLLM:
    """A fake model writing a fixed skill note with one valid citation."""

    def __init__(self, citation: str) -> None:
        """Cite ``citation`` as the valid source."""
        self.citation = citation
        self.calls = 0

    async def structured(self, task: str, prompt: Any, output: type, **_kw: Any) -> Any:
        """Return the note (with one valid and one invented citation)."""
        self.calls += 1
        note = SkillNote(
            summary="Python is a general-purpose language.",
            how_it_works="It runs your code line by line.",
            key_concepts=[Concept(name="lists", explanation="ordered collections")],
            when_to_use="Scripts, data work and backends.",
            example="print('hi')",
            qa=[
                NoteQA(
                    question="What is a list?", answer="An ordered collection.", citations=[self.citation]
                ),
                NoteQA(question="What is a dict?", answer="A key-value map.", citations=["chunk:999"]),
            ],
            citations=[self.citation, "chunk:invented"],
        )
        return SimpleNamespace(output=note, model="gpt-4o-mini")


async def test_skill_note_is_written_once_grounded_and_found_without_a_model_call(kb: Any) -> None:
    """A note keeps only real citations and is found with no model call."""
    vectors, embedder = kb
    [vec] = await embedder.embed(["Python explained: concepts, how it works, examples lists"])
    await vectors.upsert(
        PREP_CHUNKS,
        [(7, vec, {"skill_ids": ["python"], "source": "Think Python", "page_start": 12, "topic": "Lists",
                   "text": "Lists are ordered collections."})],
    )  # fmt: skip
    llm = NoteLLM("chunk:7")
    assert not await skill_notes.exists(vectors, "python")
    _note, saved = await skill_notes.write(llm, vectors, embedder, "python")  # type: ignore[arg-type]
    assert saved == 2 and await skill_notes.exists(vectors, "python")

    found = await lookup.for_skill(vectors, "python")
    assert found.note is not None and found.note["citations"] == ["chunk:7"]  # invented ids dropped
    assert found.note["source_labels"] == ["Think Python, p. 12"]
    by_question = {q["question"]: q for q in found.qa}
    assert by_question["What is a list?"]["verified"] is True
    assert (
        by_question["What is a dict?"]["verified"] is False
        and by_question["What is a dict?"]["citations"] == []
    )
    assert llm.calls == 1  # the lookup itself made no model call


async def test_prep_questions_go_to_the_bank_tagged_and_resaving_is_safe(kb: Any) -> None:
    """Prep questions are tagged; saving twice keeps one copy each."""
    vectors, embedder = kb
    questions = [
        Question(id="q1", category="technical", topic="SQL", question="How do you tune a slow SQL query?",
                 answer="Read the plan, add an index.", citations=["profile:12", "chunk:3"], verified=True),
        Question(id="q2", category="behavioral", topic="teamwork", question="Tell me about a conflict.",
                 answer="STAR story.", citations=["profile:5"]),
    ]  # fmt: skip
    items = qa_bank.from_prep_set({"id": 42, "company": "Acme", "title": "Data Engineer"}, questions)
    assert items[0]["skill_ids"] == ["sql"] and items[0]["project_ids"] == [12]
    assert items[1]["category"] == "behavioral" and items[1]["source"] == "prep:42"
    await qa_bank.add(vectors, embedder, items)
    await qa_bank.add(vectors, embedder, items)
    assert await vectors.count(QA_BANK) == 2
    hits = await qa_bank.search(vectors, embedder, "slow SQL query index", limit=1)
    assert hits[0]["company"] == "Acme"
    assert await vectors.count(SKILL_NOTES) == 0


async def test_learnability_from_material_and_closeness(kb: Any) -> None:
    """A skill near one you have is easier than an unknown one."""
    vectors, embedder = kb
    await learning.ingest(vectors, embedder)
    [vec] = await embedder.embed(["Pandas"])
    await vectors.upsert(PROFILE_ITEMS, [(1, vec, {"kind": "skill", "text": "Pandas"})])
    near = await gap.learnability(
        GapItem(skill="NumPy", skill_id="numpy", kind="must", points=7), {"python"}, embedder, vectors
    )
    assert near.learnability is not None and near.learn_reason is not None
    assert "free resources" in near.learn_reason and near.easy is (near.learnability >= 0.6)
    unknown = await gap.learnability(
        GapItem(skill="Underwater Basket Weaving", skill_id=None, kind="nice", points=2),
        set(),
        embedder,
        vectors,
    )
    assert unknown.learnability is not None and unknown.learnability < near.learnability


async def test_learnability_is_skipped_when_qdrant_is_down() -> None:
    """When Qdrant is down the gap list is returned unchanged."""

    class Down:
        """A vector store whose every call fails."""

        async def count(self, *a: Any, **k: Any) -> int:
            """Fail like an unreachable Qdrant."""
            raise ConnectionError("qdrant unreachable")

    items = [GapItem(skill="Kafka", skill_id="kafka", kind="must", points=7)]
    gap._material.pop("kafka", None)
    assert await gap.with_learnability(items, set(), HashEmbedder(), Down()) == items  # type: ignore[arg-type]


def test_quick_to_learn_comes_from_learnability_when_known() -> None:
    """The knowledge base's quick-to-learn flag decides addability."""
    assert why_addable("Kafka", "kafka", set(), easy=True, learn_reason="9 free resources") == (
        "quick to learn (9 free resources)"
    )
    assert why_addable("Kafka", "kafka", set(), easy=False) is None
