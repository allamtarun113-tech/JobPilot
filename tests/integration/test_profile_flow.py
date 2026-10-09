"""Import -> draft items -> confirmation -> every bullet backed; preferences versioning; notes parser."""

import shutil
from types import SimpleNamespace
from typing import Any

import pytest

from jobpilot.core.errors import VersionConflict
from jobpilot.db.queries import profile_items as items_repo
from jobpilot.db.session import transaction
from jobpilot.llm.client import LLMClient
from jobpilot.models.profile import ExtractedItem, ExtractedProfile, ItemKind, ItemSource
from jobpilot.profile import confirm, preferences, structure
from jobpilot.profile.parser import extract_from_notes, same_skill
from jobpilot.resume import doc as resume_doc
from tests.conftest import SAMPLE_RESUME

pytestmark = pytest.mark.usefixtures("db")


async def test_import_links_every_element_to_draft_items() -> None:
    """After import and confirm-all every resume element is backed."""
    result = await resume_doc.import_from_tex(SAMPLE_RESUME)
    assert result.items_created > 10
    loaded = await resume_doc.load_current()
    assert loaded is not None
    _, doc = loaded
    async with transaction() as conn:
        report = await confirm.backing_report(conn, doc)
        assert report.total == result.elements - 0 and report.backed == 0  # nothing confirmed yet
        await confirm.confirm_all(conn)
        report = await confirm.backing_report(conn, doc)
    assert report.ok and report.backed == report.total


async def test_rejecting_an_item_unbacks_the_bullet_that_rests_on_it() -> None:
    """Rejecting an item makes its bullet unbacked."""
    await resume_doc.import_from_tex(SAMPLE_RESUME)
    _, doc = await resume_doc.load_current()  # type: ignore[misc]
    async with transaction() as conn:
        await confirm.confirm_all(conn)
    bullet = doc.sections[4].entries[0].bullets[0]
    async with transaction() as conn:
        item = await items_repo.get(conn, bullet.evidence[0])
        assert item is not None and item["kind"] == "achievement"
        await confirm.set_status(conn, item["id"], item["version"], "rejected")
        report = await confirm.backing_report(conn, doc)
    assert report.unbacked == [bullet.id]


async def test_second_import_needs_replace() -> None:
    """A second import is refused unless replace is asked for."""
    await resume_doc.import_from_tex(SAMPLE_RESUME)
    with pytest.raises(resume_doc.ResumeExists):
        await resume_doc.import_from_tex(SAMPLE_RESUME)
    again = await resume_doc.import_from_tex(SAMPLE_RESUME, replace=True)
    assert again.resume_id == 2


async def test_stale_item_edit_is_refused() -> None:
    """Editing an item with an old version raises a conflict."""
    await resume_doc.import_from_tex(SAMPLE_RESUME)
    async with transaction() as conn:
        item = (await items_repo.list_all(conn))[0]
        await confirm.edit_text(conn, item["id"], item["version"], "edited once")
    with pytest.raises(VersionConflict):
        async with transaction() as conn:
            await confirm.edit_text(conn, item["id"], item["version"], "stale edit")


@pytest.mark.skipif(shutil.which("tectonic") is None, reason="tectonic not installed")
async def test_render_current_stores_pdf_and_report(store: Any) -> None:
    """Rendering the base resume stores a PDF and a passing report."""
    await resume_doc.import_from_tex(SAMPLE_RESUME)
    report = await resume_doc.render_current()
    assert report["ok"] and (await store.get(report["pdf_key"])).startswith(b"%PDF")
    row, _ = await resume_doc.load_current()  # type: ignore[misc]
    assert row["parse_ok"] is True and row["pdf_key"] == report["pdf_key"]


async def test_preferences_are_versioned() -> None:
    """Saving preferences with an old version raises a conflict."""
    async with transaction() as conn:
        prefs, version = await preferences.load(conn)
        assert prefs.threshold == 70 and version == 1
        prefs.threshold = 75
        assert await preferences.save(conn, prefs, version) == 2
    with pytest.raises(VersionConflict):
        async with transaction() as conn:
            await preferences.save(conn, prefs, 1)


async def test_notes_parser_drops_items_not_in_the_notes() -> None:
    """Items whose quote isn't in the notes are dropped; the rest are filed."""
    notes = "I also used Docker at Acme to ship an internal tool. Reduced build time by 30%."
    output = ExtractedProfile(
        items=[
            ExtractedItem(kind=ItemKind.SKILL, text="Docker", evidence_quote="used Docker at Acme"),
            ExtractedItem(
                kind=ItemKind.METRIC,
                text="Reduced build time by 30%",
                evidence_quote="Reduced build time by 30%",
                belongs_to="Engineer at Acme",
            ),
            ExtractedItem(
                kind=ItemKind.SKILL, text="Kubernetes", evidence_quote="ran Kubernetes clusters"
            ),  # invented
        ]
    )
    calls: list[dict[str, Any]] = []

    async def parse(**kwargs: Any) -> Any:
        """Answer like the Responses API with the canned extraction."""
        calls.append(kwargs)
        return SimpleNamespace(
            output_text=output.model_dump_json(),
            output=[],
            model=kwargs["model"],
            status="completed",
            usage=SimpleNamespace(
                input_tokens=10, output_tokens=5, input_tokens_details=SimpleNamespace(cached_tokens=0)
            ),
        )

    llm = LLMClient(api=SimpleNamespace(responses=SimpleNamespace(create=parse)))  # type: ignore[arg-type]
    drafts = await extract_from_notes(llm, notes, {"Engineer at Acme": 7})
    assert [d.text for d in drafts] == ["Docker", "Reduced build time by 30%"]
    assert drafts[1].attrs["parent_id"] == 7  # filed under the listed experience it names
    assert "- Engineer at Acme" in calls[0]["input"][0]["content"]
    assert drafts[0].attrs["skill_id"] == "docker"
    assert "<data:notes-" in calls[0]["input"][1]["content"]  # notes are fenced as data


async def test_notes_project_is_one_entry_and_known_skills_are_not_repeated() -> None:
    """A described project becomes one entry with its details under it; known skills are skipped."""
    notes = (
        "JobPilot: an AI assistant that finds jobs and scores them. "
        "Built a multi-agent system with LangGraph. Backed by 290+ automated tests. "
        "Technologies: Python, LangGraph, Lanchain, Docker"
    )
    output = ExtractedProfile(
        items=[
            ExtractedItem(
                kind=ItemKind.ACHIEVEMENT,
                text="Built a multi-agent system with LangGraph",
                evidence_quote="Built a multi-agent system with LangGraph",
                belongs_to="JobPilot",
            ),
            ExtractedItem(
                kind=ItemKind.PROJECT,
                text="JobPilot",
                evidence_quote="JobPilot: an AI assistant that finds jobs and scores them",
                about="an AI assistant that finds jobs and scores them",
                tech=["Python", "LangGraph", "Docker"],
            ),
            ExtractedItem(
                kind=ItemKind.METRIC,
                text="Backed by 290+ automated tests",
                evidence_quote="Backed by 290+ automated tests",
                belongs_to="jobpilot",
            ),
            ExtractedItem(kind=ItemKind.SKILL, text="Python", evidence_quote="Python"),  # already known
            ExtractedItem(kind=ItemKind.SKILL, text="LangGraph", evidence_quote="LangGraph"),
            ExtractedItem(kind=ItemKind.SKILL, text="Lanchain", evidence_quote="Lanchain"),  # a typo
            ExtractedItem(kind=ItemKind.SKILL, text="langgraph", evidence_quote="LangGraph"),  # said twice
            ExtractedItem(kind=ItemKind.SKILL, text="Docker", evidence_quote="Docker"),
        ]
    )

    async def parse(**kwargs: Any) -> Any:
        """Answer like the Responses API with the canned extraction."""
        return SimpleNamespace(
            output_text=output.model_dump_json(),
            output=[],
            model=kwargs["model"],
            status="completed",
            usage=SimpleNamespace(
                input_tokens=10, output_tokens=5, input_tokens_details=SimpleNamespace(cached_tokens=0)
            ),
        )

    llm = LLMClient(api=SimpleNamespace(responses=SimpleNamespace(create=parse)))  # type: ignore[arg-type]
    drafts = await extract_from_notes(llm, notes, {}, ["Python", "Lang Chain"])
    assert [(d.kind, d.text) for d in drafts] == [
        (ItemKind.PROJECT, "JobPilot"),
        (ItemKind.ACHIEVEMENT, "Built a multi-agent system with LangGraph"),
        (ItemKind.METRIC, "Backed by 290+ automated tests"),
        (ItemKind.SKILL, "LangGraph"),
        (ItemKind.SKILL, "Docker"),
    ]
    project = drafts[0]
    assert project.attrs["tech"] == ["Python", "LangGraph", "Docker"]
    assert project.attrs["about"] == "an AI assistant that finds jobs and scores them"
    assert drafts[1].attrs["parent"] == drafts[2].attrs["parent"] == project.key
    async with transaction() as conn:
        await items_repo.store_drafts(conn, drafts, ItemSource.NOTES)
        layout = await structure.layout(conn)
    (entry,) = layout.projects
    assert entry.item["text"] == "JobPilot"
    assert [d["text"] for d in entry.details] == [
        "Built a multi-agent system with LangGraph",
        "Backed by 290+ automated tests",
    ]


def test_same_skill_allows_typos_only_in_long_names() -> None:
    """Spacing, case and a one-letter typo match; short or different names don't."""
    assert same_skill("Lang Chain", "langchain") and same_skill("Langraph", "LangGraph")
    assert same_skill("Javascript (Basics)", "JavaScript")
    assert not same_skill("C", "C++") and not same_skill("Java", "Javascript")
    assert not same_skill("Qdrant", "Vector Database")
