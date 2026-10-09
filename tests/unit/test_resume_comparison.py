"""Resume comparison: wording changes and the keyword coverage delta."""

from jobpilot.models.requirements import Requirements, Seniority
from jobpilot.models.resume_doc import Bullet, Entry, Header, Layout, ResumeDoc, Section
from jobpilot.resume.comparison import compare
from jobpilot.resume.coverage import report


def test_comparison_shows_wording_and_keyword_delta() -> None:
    """The comparison lists the reworded bullet and the coverage gain."""
    before = ResumeDoc(
        header=Header(name="Candidate"),
        sections=[
            Section(
                id="s",
                title="Projects",
                layout=Layout.PROJECTS,
                entries=[Entry(id="e", title="App", bullets=[Bullet(id="b", text="Python app")])],
            )
        ],
    )
    after = before.model_copy(deep=True)
    after.sections[0].entries[0].bullets[0].text = "Python SQL app"
    requirements = Requirements(
        must_skills=["Python", "SQL"],
        nice_skills=[],
        seniority=Seniority.ENTRY,
        domain="software",
        title_norm="engineer",
        years_min=None,
        years_max=None,
        is_job_description=True,
    )
    comparison = compare(before, after, requirements)
    assert comparison["before"]["adjusted_percent"] == 50
    assert comparison["after"]["adjusted_percent"] == 100
    assert comparison["delta"] == 50
    assert comparison["changes"] == [
        {"section": "Projects / App", "before": "Python app", "after": "Python SQL app"}
    ]
    after.sections[0].entries[0].hidden = True
    assert report(after, requirements)["covered"] == []
