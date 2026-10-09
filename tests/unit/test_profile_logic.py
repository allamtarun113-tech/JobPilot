"""Draft items from the resume, /prefs parsing, TOTP, taxonomy lookup."""

import pytest

from jobpilot.core.clock import FrozenClock
from jobpilot.models.preferences import Preferences
from jobpilot.models.profile import entry_fields, fact_texts, parent_ids
from jobpilot.profile.preferences import apply_command
from jobpilot.reference import catalog
from jobpilot.resume.profile_drafts import draft_from_resume
from jobpilot.resume.tex_import import import_tex
from jobpilot.safety import totp
from tests.conftest import SAMPLE_RESUME


def test_every_resume_element_rests_on_items() -> None:
    """Every imported resume element links to draft items."""
    doc = import_tex(SAMPLE_RESUME.read_text())
    draft = draft_from_resume(doc)
    keys = {i.key for i in draft.items}
    for _, entry, bullet in doc.elements():
        linked = draft.links[bullet.id if bullet else entry.id]
        assert linked and set(linked) <= keys


def test_skills_dedupe_by_wording_and_keep_taxonomy_ids() -> None:
    """Skills merge by wording and keep their taxonomy ids."""
    draft = draft_from_resume(import_tex(SAMPLE_RESUME.read_text()))
    skills = {i.text: i for i in draft.items if i.kind == "skill"}
    assert list(skills).count("SQL") == 1
    assert skills["SQL"].attrs["categories"] == ["Languages", "Data"]
    assert "Neural Networks" in skills and "Deep Learning" in skills  # same taxonomy id, both kept
    assert skills["Neural Networks"].attrs["skill_id"] == "deep-learning"
    assert skills["Kafka"].attrs["skill_id"] == "kafka"


def test_roles_bullets_cite_achievement_and_role() -> None:
    """A role bullet rests on its achievement and its role."""
    doc = import_tex(SAMPLE_RESUME.read_text())
    draft = draft_from_resume(doc)
    by_key = {i.key: i for i in draft.items}
    role_entry = doc.sections[4].entries[0]
    bullet = role_entry.bullets[0]
    achievement, role = draft.links[bullet.id]
    assert by_key[achievement].text == bullet.text
    assert by_key[role].text == "Software Engineer at Acme Corp (july 2023–present)"
    assert by_key[role].attrs["organization"] == "Acme Corp"


def test_prefs_commands() -> None:
    """/prefs commands change one field and reject bad input."""
    p = Preferences()
    p2, change = apply_command(p, ["threshold", "60"])
    assert p2.threshold == 60 and change.before == 70
    p3, _ = apply_command(p2, ["block", "Acme", "Corp"])
    p4, _ = apply_command(p3, ["block", "acme corp"])
    assert p4.blacklist == ["Acme Corp"]
    p5, _ = apply_command(p4, ["unblock", "ACME CORP"])
    assert p5.blacklist == []
    assert apply_command(p, ["remote", "off"])[0].remote_ok is False
    for bad in (["threshold"], ["threshold", "150"], ["cards", "abc"], ["floor", "18"], ["colour", "red"]):
        with pytest.raises(ValueError):
            apply_command(p, bad)
    assert p.threshold == 70  # the original is never mutated


def test_totp_rfc6238_vector(frozen_clock: FrozenClock) -> None:
    """TOTP matches the RFC 6238 test vector."""
    from datetime import UTC, datetime

    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32("12345678901234567890")
    assert totp.code_at(secret, 59 // 30, digits=8) == "94287082"
    frozen_clock.current = datetime.fromtimestamp(59, UTC)
    assert totp.verify(secret, "287082")
    assert not totp.verify(secret, "000000")
    assert not totp.verify(secret, "abc")


def test_catalog_never_guesses() -> None:
    """Skill lookup normalizes names but never guesses."""
    assert catalog.skill_id("Lang Chain") == "langchain"
    assert catalog.skill_id("Javascript (Basics)") == "javascript"
    assert catalog.skill_id("Linux/UNIX") == "linux"
    assert catalog.skill_id("Fusion 360") is None


def _row(id: int, kind: str, text: str, attrs: dict | None = None, key: str | None = None) -> dict:
    """Return a profile item row dict."""
    return {"id": id, "kind": kind, "text": text, "attrs": attrs or {}, "import_key": key}


def test_details_belong_to_their_experience_and_facts_carry_context() -> None:
    """Details find their parent, and facts carry the parent's context."""
    rows = [
        _row(
            1,
            "role",
            "Data Scientist at Acme (Jun 2024 - Present)",
            {
                "title": "Data Scientist",
                "organization": "Acme",
                "dates": "Jun 2024 - Present",
                "tech": ["Docker", "FastAPI"],
            },
            "role:e1",
        ),
        _row(2, "achievement", "Built a ranking service", {"parent": "role:e1"}),
        _row(3, "project", "Bus System - team of three", {"name": "Bus System"}, "project:e2"),
        _row(4, "metric", "Cut latency by 40%", {"parent_id": 3}),
        _row(5, "role", "Analyst at Beta", {"title": "Analyst"}, "role:e1"),  # a later re-import, same key
        _row(6, "achievement", "Loose fact"),
    ]
    assert parent_ids(rows) == {2: 1, 4: 3}  # the importer's key resolves to the item created before it
    facts = fact_texts(rows)
    assert (
        facts[1]
        == "Data Scientist at Acme (Jun 2024 - Present). Tools and technologies used: Docker, FastAPI"
    )
    assert facts[2] == "Built a ranking service [Data Scientist at Acme (Jun 2024 - Present)]"
    assert facts[4] == "Cut latency by 40% [Bus System - team of three]"
    assert facts[6] == "Loose fact"
    assert entry_fields("project", rows[2]["text"], rows[2]["attrs"])["about"] == "team of three"
