"""Resume review loop, experience matching, HR address search, tap acknowledgement and link previews."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from jobpilot.hr_email import service
from jobpilot.matching.compute import SkillMatcher
from jobpilot.matching.profile_view import ProfileView
from jobpilot.models.resume_doc import Entry, Header, Layout, ResumeDoc, Section
from jobpilot.resume import review


def _doc() -> ResumeDoc:
    """Return a minimal resume with a summary and a skills line."""
    return ResumeDoc(
        header=Header(name="X"),
        sections=[
            Section(
                id="s0", title="Summary", layout=Layout.PARAGRAPH, entries=[Entry(id="sum", text="Analyst.")]
            ),
            Section(
                id="s1", title="Skills", layout=Layout.SKILLS, entries=[Entry(id="sk", items=["Python"])]
            ),
        ],
    )


def test_review_summary_line() -> None:
    """The review summary line says ready, or what to check."""
    assert review.summary({"score": 96, "passed": True}) == "Review: 96/100 - ready to send"
    line = review.summary({"score": 70, "passed": False, "issues": [{"problem": "weak bullets"}]})
    assert line == "Review: 70/100 - check before approving (weak bullets)"
    assert review.summary(None) == "Review: not available"


def test_a_skill_named_in_your_projects_counts_as_matched() -> None:
    """A skill named in your experience counts, by whole word only."""
    view = ProfileView(context_items={7: "Built an MCP server with automated debugging and C++ tooling"})
    matcher = SkillMatcher(view, embedder=None)  # type: ignore[arg-type]
    assert matcher.in_experience("debugging", "debugging") == [7]
    assert matcher.in_experience("C++", "cpp") == [7]
    assert matcher.in_experience("Rust", "rust") == []
    assert matcher.in_experience("Go", "go") == []  # 'go' never matches inside other words


def test_company_domain_keys_reject_other_companies() -> None:
    """Domain keys match the company's own domain only."""
    keys = service.company_keys("El-shaddai Recruitment And Ites Services")
    assert keys == ["elshaddai", "elshaddairecruitment"]
    assert service.belongs("elshaddaihr.com", keys)
    hire = service.company_keys("HireVault")
    assert service.belongs("hirevault.io", hire) and not service.belongs("hireson.com", hire)
    assert service.company_keys("IT Services Pvt Ltd") == []  # nothing distinctive: never guess


def test_only_the_companys_own_addresses_are_kept_from_what_the_agent_saw() -> None:
    """Only own-domain addresses are kept; the hiring inbox is chosen."""
    seen = [
        {"email": "jobs@hireson.com", "source": "web search (https://hireson.com/contact)"},
        {"email": "info@elshaddaihr.com", "source": "https://elshaddaihr.com"},
        {"email": "careers@elshaddaihr.com", "source": "https://elshaddaihr.com/careers"},
        {"email": "priya.k@elshaddaihr.com", "source": "https://elshaddaihr.com/team"},
    ]
    found = service.verified(seen, "El-shaddai Recruitment And Ites Services")
    emails = {a.email for a in found}
    assert "jobs@hireson.com" not in emails  # another company's domain
    assert {"info@elshaddaihr.com", "careers@elshaddaihr.com", "priya.k@elshaddaihr.com"} == emails
    chosen = service.choose(found)
    assert chosen is not None and chosen.email == "careers@elshaddaihr.com"  # hiring inbox first
    assert service.verified(seen, "IT Services Pvt Ltd") == []  # no distinctive name: never guess


def test_a_general_inbox_is_the_labelled_fallback_and_a_person_never_is() -> None:
    """A general inbox is a labelled fallback; a person is never chosen."""
    from jobpilot.hr_email import address_finder

    general = address_finder.find("info@acme.com", "acme.com")
    chosen = service.choose(general)
    assert chosen is not None and chosen.email == "info@acme.com" and "general inbox" in chosen.source
    assert service.choose(address_finder.find("priya.k@acme.com", "acme.com")) is None


async def test_tap_acknowledged_at_once_for_the_owner_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the owner's taps are acknowledged."""
    from jobpilot.worker import telegram_poller

    posted: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Record the Bot API call and answer ok."""
        posted.append({"url": str(request.url), "body": request.content.decode()})
        return httpx.Response(200, json={"ok": True, "result": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tap = {"callback_query": {"id": "q1", "data": "ap:x", "message": {"chat": {"id": 4242}}}}
        await telegram_poller._acknowledge(client, "T", tap)
        stranger = {"callback_query": {"id": "q2", "data": "ap:x", "message": {"chat": {"id": 1}}}}
        await telegram_poller._acknowledge(client, "T", stranger)
        await telegram_poller._acknowledge(client, "T", {"message": {"text": "hi"}})
    assert len(posted) == 1 and posted[0]["url"].endswith("/botT/answerCallbackQuery")
    assert '"q1"' in posted[0]["body"]


async def test_cards_and_edits_never_show_link_previews(monkeypatch: pytest.MonkeyPatch) -> None:
    """Messages and edits always disable link previews."""
    from jobpilot.servers.telegram import server, tools

    sent: list[tuple[str, dict[str, Any]]] = []

    async def call(method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Record the Bot API call instead of sending it."""
        sent.append((method, params))
        return {"ok": True, "message_id": 1}

    monkeypatch.setattr(tools, "call", call)
    monkeypatch.setattr(server.auth, "locked_chat_id", lambda chat_id: 4242)
    monkeypatch.setattr(server.auth, "check_scope", lambda tool: None)
    await server.send_message("Learn Go: https://www.youtube.com/watch?v=x")
    await server.edit_message(5, "edited https://www.youtube.com/watch?v=x")
    assert [m for m, _ in sent] == ["sendMessage", "editMessageText"]
    assert all(p["link_preview_options"] == {"is_disabled": True} for _, p in sent)


def test_email_checks_refuse_placeholders_and_writers_get_the_claimable_skills() -> None:
    """Placeholders are refused; writers get the claimable skills list."""
    from jobpilot.safety import claims, email_checks

    facts = {1: "Built Python and SQL reports", 2: "Docker"}
    errors = email_checks.check("Hi, I built Python reports. Best, [Your Name]", [1], facts)
    assert "placeholder text like [Name] left in" in errors
    assert claims.claimable_skills(facts, ("Kafka",)) == ["Docker", "Kafka", "Python", "SQL"]
