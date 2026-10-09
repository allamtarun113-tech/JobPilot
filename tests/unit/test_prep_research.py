"""The Prep Research agent keeps only notes from pages it fetched."""

from types import SimpleNamespace

from jobpilot.models.prep import ResearchNote, Topic
from jobpilot.prep.research import Decision, PrepResearchAgent


class SequenceLLM:
    """A fake model that searches, fetches one page, then finishes."""

    def __init__(self) -> None:
        """Script the three decisions (one note cites an unfetched page)."""
        self.decisions = [
            Decision(action="search", query="Acme data analyst interview experience"),
            Decision(action="fetch", url="https://example.com/interview"),
            Decision(
                action="finish",
                notes=[
                    ResearchNote(
                        topic="SQL",
                        note="Candidates report SQL window questions.",
                        source_url="https://example.com/interview",
                    ),
                    ResearchNote(
                        topic="Bad",
                        note="This URL was never fetched.",
                        source_url="https://untrusted.example/post",
                    ),
                ],
            ),
        ]

    async def structured(self, *_args, **_kwargs):
        """Return the next scripted decision."""
        return SimpleNamespace(output=self.decisions.pop(0), input_tokens=10, output_tokens=10)


class FakeWeb:
    """A fake web connection with one search result and its page."""

    async def call(self, tool, arguments):
        """Answer a search or fetch for the interview page."""
        if tool == "search":
            return {
                "ok": True,
                "results": [{"title": "Interview", "url": "https://example.com/interview"}],
            }
        assert arguments["url"] == "https://example.com/interview"
        return {"ok": True, "page": {"title": "Interview", "text": "SQL interview notes"}}


async def test_research_only_returns_notes_from_searched_and_fetched_pages() -> None:
    """A note citing a page that wasn't fetched is dropped."""
    agent = PrepResearchAgent(SequenceLLM(), FakeWeb())  # type: ignore[arg-type]
    notes = await agent.run(
        "Acme",
        "Data Analyst",
        [Topic(name="SQL", category="technical", priority=1, skill_ids=["sql"], reason="gap")],
    )
    assert [note.source_url for note in notes] == ["https://example.com/interview"]
