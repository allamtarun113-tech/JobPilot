"""Prep: the book manifest, heading-aware splitting, DSA picks and citations."""

from evals.runners import prep as prep_eval
from jobpilot.models.prep import PackQuestion, Topic
from jobpilot.prep import dsa_picker, ingest, qa_bank, study_plan


def test_manifest_has_32_prep_books_and_three_need_heading_heuristic() -> None:
    """32 prep books are listed; 3 have no bookmarks."""
    books = ingest.manifest()
    assert len(books) == 32
    assert sum(not book["has_bookmarks"] for book in books) == 3


def test_bookmark_and_heuristic_splitting_preserve_headings_and_pages() -> None:
    """Both splitters cut at headings and keep page numbers."""
    bookmark_chunks = ingest.split_pages(
        [
            "Chapter One\nalpha details with enough explanatory content for a useful section",
            "Section Two\nbeta details with enough explanatory content for a useful section",
        ],
        book_id="book",
        source="Book",
        skill_ids=["python"],
        headings={0: ["Chapter One"], 1: ["Section Two"]},
    )
    assert [(chunk.topic, chunk.page_start, chunk.page_end) for chunk in bookmark_chunks] == [
        ("Chapter One", 1, 2),
        ("Section Two", 2, 2),
    ]
    heuristic = ingest.split_pages(
        [
            "1. Arrays\narray body text with enough explanatory content for this section",
            "2. Graphs\ngraph body text with enough explanatory content for this section",
        ],
        book_id="questions",
        source="Questions",
        skill_ids=["dsa"],
        headings=None,
    )
    assert [chunk.topic for chunk in heuristic] == ["1. Arrays", "2. Graphs"]


def test_dsa_picker_citation_checks_and_weak_study_plan() -> None:
    """DSA picks fit the topic; unknown citations drop; weak topics plan study."""
    topics = [Topic(name="SQL", category="technical", priority=1, skill_ids=["sql"], reason="JD gap")]
    assert dsa_picker.pick(topics, [])[0]["pattern"] == "Relational query decomposition"
    questions = [
        PackQuestion(
            group="skill",
            about="SQL",
            category="technical",
            question="How do windows work?",
            answer="They retain rows.",
            citations=["chunk:7"],
        ),
        PackQuestion(
            group="job",
            category="company",
            question="What do they ask?",
            answer="Unknown.",
            citations=["web:https://wrong.example"],
        ),
    ]
    labels = {"chunk:7": "SQL Book, p. 7"}
    checked = qa_bank.from_pack(questions, {"id": 1}, labels, set(labels))
    assert checked[0]["verified"] and checked[0]["source_labels"] == ["SQL Book, p. 7"]
    assert not checked[1]["verified"] and checked[1]["citations"] == []  # an unknown source is dropped
    assert checked[0]["source"] == "skill_qa:sql" and checked[1]["source"] == "prep:1"
    rows = [{"topic": "SQL", "known": False}]
    plan = study_plan.build(rows, ["sql"])
    assert plan["weak_topics"] == ["SQL"]
    assert plan["projects"] and plan["checkpoints"]


def test_shared_groups_are_filed_once_for_every_job() -> None:
    """HR and experience Q&A are filed once for all jobs."""
    assert qa_bank.group_source("hr", "", 5) == qa_bank.group_source("hr", "", 9) == "hr"
    assert qa_bank.group_source("experience", "", 5) == "experience"
    assert qa_bank.group_source("project", "profile:12", 5) == "project:12"


def test_prep_eval_gate_passes() -> None:
    """The offline prep eval passes."""
    summary, failures = prep_eval.run()
    assert summary["citation_boundary_pass_rate"] == 1.0
    assert not failures
