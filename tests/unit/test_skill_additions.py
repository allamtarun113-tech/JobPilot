"""Missing job skills the tailored resume may add.

Only skills related to yours or quick to learn are added, and the score
after the change is exact (before + each added skill's gap points).
"""

from jobpilot.models.resume_doc import Entry, Header, Layout, ResumeDoc, Section
from jobpilot.models.resume_ops import AddSkill, RewriteSummary
from jobpilot.resume import diff, ops, skill_additions


def _doc() -> ResumeDoc:
    """Return a resume with a summary and two skills lines."""
    return ResumeDoc(
        header=Header(name="Test"),
        sections=[
            Section(
                id="s0",
                title="Summary",
                layout=Layout.PARAGRAPH,
                entries=[Entry(id="e0", text="Python developer building data tools.", evidence=[1])],
            ),
            Section(
                id="s1",
                title="Skills",
                layout=Layout.SKILLS,
                entries=[
                    Entry(id="e1", title="Languages", items=["Python", "SQL"]),
                    Entry(id="e2", title="Tools", items=["Linux"]),
                ],
            ),
        ],
    )


GAP = [
    {"skill": "FastAPI", "skill_id": "fastapi", "kind": "must", "points": 7.0},  # implies python: related
    {"skill": "Git", "skill_id": "git", "kind": "must", "points": 7.0},  # depth 0: quick to learn
    {"skill": "Kafka", "skill_id": "kafka", "kind": "must", "points": 7.0},  # deep, unrelated
    {"skill": "Communication", "skill_id": "communication", "kind": "nice", "points": 2.5},  # soft: never
    {"skill": "Fintech", "skill_id": "fintech", "kind": "nice", "points": 2.5},  # domain: never
    {"skill": "Some Internal Tool", "skill_id": None, "kind": "must", "points": 7.0},  # unknown: never
]


def test_only_related_or_easy_catalog_skills_are_addable() -> None:
    """Only related or quick-to-learn taxonomy skills are addable."""
    addable = {a["skill"]: a["reason"] for a in skill_additions.eligible(GAP, _doc())}
    assert set(addable) == {"FastAPI", "Git"}
    assert addable["FastAPI"].startswith("related to") and addable["Git"] == "quick to learn"


def test_add_skill_is_checked_and_the_summary_may_mention_it() -> None:
    """Added skills are checked and the summary may then name them."""
    plan = [
        AddSkill(op="add_skill", entry_id="e1", skill="FastAPI"),
        AddSkill(op="add_skill", entry_id="e1", skill="Kafka"),  # not addable
        AddSkill(op="add_skill", entry_id="e0", skill="Git"),  # not a skills line
        AddSkill(op="add_skill", entry_id="e2", skill="Python"),  # already listed
        RewriteSummary(
            op="rewrite_summary",
            entry_id="e0",
            text="Python developer building FastAPI data tools.",
            evidence=[1],
        ),
    ]
    doc, applied, rejected = ops.apply(
        _doc(), plan, {1: "Python developer building data tools"}, ("FastAPI", "Git")
    )
    assert doc.sections[1].entries[0].items == ["Python", "SQL", "FastAPI"]
    assert "FastAPI" in (doc.sections[0].entries[0].text or "")
    assert len(applied) == 2 and len(rejected) == 3
    assert "added FastAPI" in diff.summary(diff.describe(_doc(), doc))


def test_boost_is_exact() -> None:
    """The boosted score adds each skill's points, capped at 100."""
    operations = [{"op": "add_skill", "skill": "FastAPI"}, {"op": "add_skill", "skill": "Git"}]
    boost = skill_additions.boost(operations, GAP, 61.2)
    assert boost == {
        "added": [{"skill": "FastAPI", "points": 7.0}, {"skill": "Git", "points": 7.0}],
        "before": 61.2,
        "after": 75.2,
    }
    assert skill_additions.boost(operations, GAP, 95.0)["after"] == 100.0  # capped


def test_change_summary_reads_like_a_person_wrote_it() -> None:
    """The change summary is short and readable."""
    assert diff.summary(["No tailoring changes; base resume retained"]) == "no changes (original resume)"
    assert diff.summary(["added skill Git (Tools)", "bullet b1 reworded", "bullet b2 reworded"]) == (
        "added Git · 2 bullets reworded"
    )
