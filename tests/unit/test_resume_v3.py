# ruff: noqa: E501
"""Resume v3 (decision 36): the pool, the faithful-rewrite checks, the composer and the tailoring loop.

No model is called: the composer's answers are scripted, including the mistakes the LaTeX editor of resume
v2 made on real jobs (merged facts, a new project pushed under an old one, inflated verbs and results).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jobpilot.core.errors import LLMInvalidOutput
from jobpilot.models.resume_doc import Bullet, Entry, Header, Layout, ResumeDoc, Section
from jobpilot.models.resume_plan import BulletFixes, ResumePlan
from jobpilot.models.resume_review import ResumeReview, ReviewIssue
from jobpilot.reference import catalog
from jobpilot.resume import compose, pool, review, tailor, tex_versions, verifier
from jobpilot.resume.render import render_tex
from jobpilot.safety import fidelity

SUMMARY = "Analyst who likes data and Python."


def doc() -> ResumeDoc:
    """A small base resume: summary, education, skills, one project and two roles."""
    return ResumeDoc(
        header=Header(name="Asha Rao"),
        sections=[
            Section(
                id="s0",
                title="Career Objective",
                layout=Layout.PARAGRAPH,
                entries=[Entry(id="e0", text=SUMMARY, evidence=[1])],
            ),
            Section(
                id="s1",
                title="Education",
                layout=Layout.DATED_PAIRS,
                entries=[
                    Entry(id="ed", title="B.Tech", date="2024", subtitle="IIT Palakkad", detail="7.2 CGPA")
                ],
            ),
            Section(
                id="s2",
                title="Technical Skills",
                layout=Layout.SKILLS,
                entries=[
                    Entry(id="sk1", title="Languages", items=["Python", "SQL"], evidence=[20, 21]),
                    Entry(id="sk2", title="Design Tools", items=["AutoCad"], evidence=[22]),
                ],
            ),
            Section(
                id="s3",
                title="Projects",
                layout=Layout.PROJECTS,
                entries=[
                    Entry(
                        id="e13",
                        title="Bus Booking Site",
                        note="Built in a team of three.",
                        evidence=[30],
                        bullets=[
                            Bullet(
                                id="b1",
                                text="Developed a web page using HTML, CSS and Python-Flask with "
                                "MySQL, with login and ticket booking",
                                evidence=[31, 30],
                            )
                        ],
                    ),
                ],
            ),
            Section(
                id="s4",
                title="Experience",
                layout=Layout.ROLES,
                entries=[
                    Entry(
                        id="e15",
                        title="Turing - Data Scientist",
                        date="2024--present",
                        evidence=[40],
                        bullets=[
                            Bullet(
                                id="b4",
                                text="Worked in Gen AI projects focused on training large language models "
                                "(LLMs) through RLHF and SFT tasks.",
                                evidence=[41, 40],
                            )
                        ],
                    ),
                    Entry(
                        id="e16",
                        title="BEML - Student Intern",
                        date="2023",
                        evidence=[50],
                        bullets=[
                            Bullet(
                                id="b5",
                                text="Learned about BEML projects like TATRA vehicle design and manufacturing.",
                                evidence=[51, 50],
                            )
                        ],
                    ),
                ],
            ),
        ],
    )


def row(i: int, kind: str, text: str, **attrs: Any) -> dict[str, Any]:
    """A confirmed profile item row."""
    return {"id": i, "kind": kind, "status": "confirmed", "text": text, "attrs": attrs, "import_key": None}


ROWS = [
    row(1, "summary", SUMMARY),
    row(20, "skill", "Python"),
    row(21, "skill", "SQL"),
    row(22, "skill", "AutoCad"),
    row(23, "skill", "LangGraph"),
    row(24, "skill", "GENAI", skill_id="llms"),
    row(25, "skill", "AGENTIC AI"),
    row(30, "project", "Bus Booking Site", name="Bus Booking Site"),
    row(31, "achievement", "Developed a web page ...", parent_id=30),
    row(40, "role", "Data Scientist at Turing", title="Data Scientist", organization="Turing"),
    row(41, "achievement", "Worked in Gen AI projects ...", parent_id=40),
    row(
        42,
        "achievement",
        "Worked as a **Quality Lead** on a project where we developed AI agents that use an LLM as a judge.",
        parent_id=40,
    ),
    row(
        43,
        "achievement",
        "Worked in the R&D team, researching benchmarks and evaluating LLM models.",
        parent_id=40,
    ),
    row(50, "role", "Student Intern at BEML", title="Student Intern", organization="BEML"),
    row(51, "achievement", "Learned about BEML projects ...", parent_id=50),
    row(
        60,
        "project",
        "JobPilot",
        name="JobPilot",
        tech=["Python", "LangGraph", "FastAPI"],
        about="An AI assistant that finds and scores jobs.",
    ),
    row(61, "achievement", "Built a multi-agent system with LangGraph to search for jobs.", parent_id=60),
    row(62, "achievement", "Deployed a FastAPI dashboard backed by 290+ automated tests.", parent_id=60),
]
POOL = pool.build(doc(), ROWS)


def plan(**fields: Any) -> ResumePlan:
    """A composer plan; unspecified parts are empty."""
    return ResumePlan.model_validate(
        {"summary": "", "projects": [], "experience": [], "skills": [], **fields}
    )


def entry_of(d: ResumeDoc, entry_id: str) -> Entry:
    """The entry with this id."""
    return next(e for s in d.sections for e in s.entries if e.id == entry_id)


# ---- the pool ----------------------------------------------------------------------------------------


def test_every_confirmed_detail_is_its_own_bullet_under_its_own_entry() -> None:
    """Turing's two extra details become two bullets of Turing; JobPilot is a new project with its details."""
    turing = POOL.entry("e15")
    assert turing is not None and [b.id for b in turing.bullets] == ["b4", "p42", "p43"]
    assert turing.bullets[1].text.startswith("Worked as a Quality Lead")  # markdown removed
    jobpilot = POOL.entry("p60")
    assert jobpilot is not None and not jobpilot.on_base and jobpilot.kind == "project"
    assert [b.id for b in jobpilot.bullets] == ["p61", "p62"] and jobpilot.note.startswith("An AI assistant")
    assert "FastAPI" in jobpilot.context  # the project's own tools back its bullets
    bus = POOL.entry("e13")
    assert bus is not None and [b.id for b in bus.bullets] == ["b1"]  # nothing from JobPilot under Bus


def test_confirmed_skills_missing_from_the_resume_are_offered_with_tidy_names() -> None:
    """Skills typed in notes are offered once, with readable names."""
    assert POOL.extra_skills == ["LangGraph", "Generative AI", "Agentic AI"]
    assert catalog.display_skill("GIT") == "Git" and catalog.display_skill("CLAUDE Code") == "Claude Code"


# ---- faithful rewrites (the screenshots' mistakes) ---------------------------------------------------

B1 = POOL.entry("e13").bullets[0].text  # type: ignore[union-attr]
B4 = POOL.entry("e15").bullets[0].text  # type: ignore[union-attr]
B5 = POOL.entry("e16").bullets[0].text  # type: ignore[union-attr]
PLANNED = "We will develop schemes with low-dispersion properties using FENICSX."


@pytest.mark.parametrize(
    ("new", "source", "reason"),
    [
        (
            "Led Gen AI projects to train LLMs through RLHF and SFT, ensuring high-quality outputs.",
            B4,
            "adds",
        ),
        (
            "Spearheaded Gen AI projects training LLMs through RLHF and SFT, achieving impactful results.",
            B4,
            "adds",
        ),
        (
            "Contributed to BEML projects like TATRA in the design and manufacturing of vehicles.",
            B5,
            "contributed",
        ),
        ("Designed and manufactured BEML vehicles like TATRA.", B5, "changes what you did"),
        (
            "Developed a web application using FastAPI, HTML, CSS and MySQL, enhancing booking efficiency.",
            B1,
            "skill 'FastAPI'",
        ),
        (
            "Developed a web page using HTML, CSS, Python-Flask and MySQL, improving user satisfaction.",
            B1,
            "adds words",
        ),
        ("Developed schemes with low-dispersion properties using FENICSX.", PLANNED, "planned work"),
        ("Worked in Gen AI projects training LLMs, improving accuracy by 40%.", B4, "number"),
        ("Worked on LLMs.", B4, "drops too much"),
        ("Worked in Gen AI projects. Worked in Gen AI projects.", "Worked in Gen AI projects.", "repeats"),
        ("Built **AI** agents", "Built AI agents", "formatting"),
    ],
)
def test_unfaithful_rewrites_are_refused(new: str, source: str, reason: str) -> None:
    """Each inflated, merged or changed rewrite is refused with its reason."""
    problem = fidelity.rewrite_problem(new, source)
    assert problem is not None and reason in problem, problem


@pytest.mark.parametrize(
    ("new", "source"),
    [
        ("Trained large language models (LLMs) through RLHF and SFT in Gen AI projects.", B4),
        ("Developed a web page with HTML, CSS, Python-Flask and MySQL, with login and ticket booking.", B1),
        (
            "Worked as Project Lead; started the project from scratch and successfully delivered it as per client "
            "requirements.",
            "worked as a Project Lead for a project. Started the project from scratch adn succefully delivered the "
            "project as per cliend requirements.",
        ),
        (
            "Researched benchmarks and evaluated LLM models in the R&D team.",
            "Worked in the R&D team, researching benchmarks and evaluating LLM models.",
        ),
        ("Learned about BEML projects like TATRA vehicle design and manufacturing.", B5),
        ("Will develop schemes with low-dispersion properties using FENICSX.", PLANNED),
    ],
)
def test_faithful_rewrites_pass(new: str, source: str) -> None:
    """Reordering, trimming, typo fixes and a plain verb are allowed."""
    assert fidelity.rewrite_problem(new, source) is None


FACT_TEXTS = [r["text"] for r in ROWS]


@pytest.mark.parametrize(
    ("summary", "reason"),
    [
        (
            "Generative AI Developer with experience in Python, SQL and LangGraph across Gen AI projects and web "
            "pages.",
            "calls you a developer",
        ),
        (
            "Data person with strong expertise in Python and SQL, building Gen AI projects and web pages with "
            "LangGraph.",
            "puffery",
        ),
        (
            "Data person with Python, SQL and Kubernetes experience, building Gen AI projects and web pages with "
            "LangGraph.",
            "skill 'Kubernetes'",
        ),
        (
            "Data person who improved model accuracy for Gen AI projects using Python, SQL and LangGraph in "
            "web pages.",
            "impact",
        ),
        (
            "Senior data person with Python and SQL experience across Gen AI projects, web pages and LangGraph "
            "agents.",
            "title",
        ),
        (
            "Data person with 5 years of Python and SQL experience across Gen AI projects and LangGraph agents.",
            "number",
        ),
        ("Too short.", "15-70 words"),
    ],
)
def test_bad_summaries_are_refused(summary: str, reason: str) -> None:
    """Titles, puffery, impact claims, unconfirmed skills and numbers keep the original summary."""
    problem = fidelity.summary_problem(summary, FACT_TEXTS, SUMMARY, ["Data Scientist", "Student Intern"])
    assert problem is not None and reason in problem, problem


def test_a_plain_true_summary_passes() -> None:
    """A summary that names real work and confirmed skills is used."""
    text = (
        "Data Scientist at Turing working on Gen AI projects that train large language models through RLHF "
        "and SFT. Built JobPilot, a multi-agent system with LangGraph, Python and FastAPI."
    )
    assert fidelity.summary_problem(text, FACT_TEXTS, SUMMARY, ["Data Scientist"]) is None


# ---- the composer --------------------------------------------------------------------------------------


def test_the_composer_adds_the_new_project_and_keeps_every_detail_separate() -> None:
    """A good plan: JobPilot first, all Turing details as their own bullets, true rewordings applied."""
    good = plan(
        summary="Data Scientist at Turing working on Gen AI projects that train large language models through "
        "RLHF and SFT. Built JobPilot, a multi-agent system with LangGraph, Python and FastAPI.",
        projects=[
            {"id": "p60", "bullets": [{"id": "p61", "text": ""}, {"id": "p62", "text": ""}]},
            {"id": "e13", "bullets": [{"id": "b1", "text": ""}]},
        ],
        experience=[
            {
                "id": "e15",
                "bullets": [
                    {
                        "id": "p42",
                        "text": "Worked as Quality Lead on a project that developed AI agents using an LLM as a "
                        "judge.",
                    },
                    {
                        "id": "b4",
                        "text": "Trained large language models (LLMs) through RLHF and SFT in Gen AI projects.",
                    },
                ],
            }
        ],
        skills=[
            {"label": "AI Tools", "items": ["LangGraph", "Generative AI", "Agentic AI"]},
            {"label": "Languages", "items": ["Python", "SQL"]},
        ],
    )
    out = compose.compose(doc(), POOL, good)
    assert out.rejected == [] and out.doc.composed
    projects = next(s for s in out.doc.sections if s.layout == Layout.PROJECTS)
    assert [e.id for e in projects.entries] == ["p60", "e13"]
    turing = entry_of(out.doc, "e15")
    assert [b.id for b in turing.bullets] == ["p42", "b4", "p43"]  # forgotten p43 added back, never merged
    assert turing.bullets[1].text.startswith("Trained large language models")
    assert entry_of(out.doc, "e0").text.startswith("Data Scientist at Turing")
    skills = next(s for s in out.doc.sections if s.layout == Layout.SKILLS)
    assert [(e.title, e.items) for e in skills.entries] == [
        ("AI Tools", ["LangGraph", "Generative AI", "Agentic AI"]),
        ("Languages", ["Python", "SQL"]),
        ("Design Tools", ["AutoCad"]),  # left out by the plan: kept, last (the page limit may drop it)
    ]
    assert entry_of(out.doc, "ed") == entry_of(doc(), "ed")  # education untouched
    reasons = " ".join(a["reason"] for a in out.applied)
    assert "added project JobPilot" in reasons and "added your confirmed details" in reasons


def test_the_screenshot_mistakes_are_all_refused() -> None:
    """A bad plan changes nothing untrue: every false line keeps its original wording."""
    bad = plan(
        summary="Generative AI Developer with strong expertise in LLMs and Prompt Engineering. Generative AI "
        "Developer with strong expertise in LLMs and Prompt Engineering.",
        projects=[
            {
                "id": "e13",
                "bullets": [
                    {
                        "id": "b1",
                        "text": "Developed a web application using FastAPI and MySQL, enhancing booking efficiency.",
                    },
                    {
                        "id": "p61",
                        "text": "Developed a multi-agent system with LangGraph.",
                    },  # JobPilot's fact under Bus
                ],
            }
        ],
        experience=[
            {
                "id": "e15",
                "bullets": [
                    {"id": "b4", "text": "Led Gen AI projects to train LLMs, ensuring high-quality outputs."}
                ],
            },
            {
                "id": "e16",
                "bullets": [{"id": "b5", "text": "Gained experience in API Development for vehicle design."}],
            },
        ],
        skills=[{"label": "Languages", "items": ["TensorFlow", "Python"]}],
    )
    out = compose.compose(doc(), POOL, bad)
    assert entry_of(out.doc, "e0").text == SUMMARY
    assert entry_of(out.doc, "e13").bullets[0].text == B1 and len(entry_of(out.doc, "e13").bullets) == 1
    assert entry_of(out.doc, "e15").bullets[0].text == B4 and entry_of(out.doc, "e16").bullets[0].text == B5
    assert "TensorFlow" not in tex_versions.plain(render_tex(out.doc))
    assert len(out.rejected) == 6 and not [a for a in out.applied if a["op"] == "rewrite_bullet"]
    projects = next(s for s in out.doc.sections if s.layout == Layout.PROJECTS)
    assert [e.id for e in projects.entries] == ["e13", "p60"]  # JobPilot kept, after the planned one


def test_a_reworded_line_may_not_repeat_another() -> None:
    """Two bullets saying the same thing: the copy keeps its original."""
    same = plan(
        experience=[
            {
                "id": "e15",
                "bullets": [
                    {"id": "b4", "text": ""},
                    {
                        "id": "p43",
                        "text": "Worked in Gen AI projects training large language models (LLMs) through RLHF and SFT "
                        "tasks.",
                    },
                ],
            }
        ]
    )
    out = compose.compose(doc(), POOL, same)
    assert any("p43" in r for r in out.rejected)


def test_nothing_is_ever_removed_from_the_resume() -> None:
    """A plan that names one project, one bullet and one skills line still keeps everything."""
    p = plan(
        projects=[{"id": "p60", "bullets": [{"id": "p62", "text": ""}]}],
        experience=[{"id": "e15", "bullets": [{"id": "p43", "text": ""}]}],
        skills=[{"label": "Languages", "items": ["Python"]}],
    )
    out = compose.compose(doc(), POOL, p)
    projects = next(s for s in out.doc.sections if s.layout == Layout.PROJECTS)
    assert [e.id for e in projects.entries] == ["p60", "e13"] and len(entry_of(out.doc, "p60").bullets) == 2
    assert [b.id for b in entry_of(out.doc, "e15").bullets] == ["p43", "b4", "p42"]
    assert len(entry_of(out.doc, "e16").bullets) == 1
    skills = {e.title: e.items for s in out.doc.sections if s.layout == Layout.SKILLS for e in s.entries}
    assert skills["Languages"] == ["Python", "SQL"] and skills["Design Tools"] == ["AutoCad"]
    assert skills["AI and LLM Tools"] == [
        "LangGraph",
        "Generative AI",
        "Agentic AI",
    ]  # every confirmed skill
    assert not any(e.hidden for s in out.doc.sections for e in s.entries)
    assert not any(b.hidden for _s, _e, b in out.doc.elements() if b)


def test_the_jobs_required_skills_come_first() -> None:
    """Required skills lead each line, then nice-to-have; lines with required skills move up."""
    out = compose.compose(doc(), POOL, plan(), required=["SQL", "LangGraph"], nice=["Agentic AI"])
    skills = [(e.title, e.items) for s in out.doc.sections if s.layout == Layout.SKILLS for e in s.entries]
    assert skills == [
        ("Languages", ["SQL", "Python"]),  # a required skill: first line, required skill first
        ("AI and LLM Tools", ["LangGraph", "Agentic AI", "Generative AI"]),  # required, nice, rest
        ("Design Tools", ["AutoCad"]),  # no job skill: last
    ]


def test_spelling_capitals_and_dates_are_fixed_by_code() -> None:
    """Typos, a lower-case start, a stray full stop and lower-case months are polished; names are kept."""
    base = doc()
    roles = next(s for s in base.sections if s.layout == Layout.ROLES)
    roles.entries[0].title, roles.entries[0].date = "Turing - Data Scientist.", "june 2024--present"
    rows = [*ROWS, row(45, "achievement", "worked as a Project Lead and succefully delivered it as per cliend "
                       "requirements adn deadlines.", parent_id=40)]  # fmt: skip
    out = compose.compose(base, pool.build(base, rows), plan())
    turing = entry_of(out.doc, "e15")
    assert turing.title == "Turing - Data Scientist" and turing.date == "June 2024--Present"
    fixed = next(b.text for b in turing.bullets if b.id == "p45")
    assert (
        fixed
        == "Worked as a Project Lead and successfully delivered it as per client requirements and deadlines."
    )
    assert "RLHF and SFT" in turing.bullets[0].text  # acronyms untouched


# ---- the loop --------------------------------------------------------------------------------------------


class ScriptedLLM:
    """A fake model returning scripted plans and reviews."""

    def __init__(self, plans: list[Any], reviews: list[Any]) -> None:
        """Use these plans and reviews in order."""
        self.plans, self.reviews, self.prompts = plans, reviews, []
        self.fixes: list[Any] = []

    async def structured(self, task: str, prompt: Any, output: type, **_: Any) -> Any:
        """Return (or raise) the next plan, fix or review."""
        self.prompts.append(prompt)
        if output is BulletFixes:
            item = self.fixes.pop(0) if self.fixes else BulletFixes(bullets=[])
            return SimpleNamespace(output=item, prompt_id="p", model="m")
        item = (self.plans if output is ResumePlan else self.reviews).pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(output=item, prompt_id="p", model="m")


PAGE_LINES = {"lines": 40}  # lines of plain text that fit on one fake page


@pytest.fixture
def fake_build(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Skip Tectonic: the 'PDF' is the plain text; more than PAGE_LINES lines counts as two pages."""
    built: list[str] = []

    async def verify(tex: str, applied: list[dict[str, Any]], *, name: str, max_pages: int):  # type: ignore[no-untyped-def]
        """Build the 'PDF' as plain text and run the real verifier rules."""
        built.append(tex)
        text = name + "\n" + tex_versions.plain(tex[tex.find("\\begin{document}") :])
        pages = 1 + text.count("\n") // PAGE_LINES["lines"]
        return text.encode(), verifier.check_text(text, pages, tex, applied, name=name, max_pages=max_pages)

    async def read_back(pdf: bytes) -> str:
        """Return the fake PDF's text."""
        return pdf.decode()

    monkeypatch.setattr(verifier, "verify", verify)
    monkeypatch.setattr(verifier, "read_back", read_back)
    return built


async def run(llm: ScriptedLLM, **kw: Any) -> tailor.Tailored:
    """Tailor the test resume with the scripted model."""
    return await tailor.tailor(
        llm,
        doc(),
        job={"title": "AI Engineer"},
        profile_items={1: SUMMARY},  # type: ignore[arg-type]
        rows=ROWS,
        run_id=None,
        **kw,
    )


GOOD_B4 = "Trained large language models (LLMs) through RLHF and SFT in Gen AI projects."


async def test_review_issues_reach_the_next_round_and_the_better_round_wins(fake_build: list[str]) -> None:
    """A weak review sends its issues back; the better second round is kept."""
    weak = ResumeReview(
        score=80,
        ready=False,
        issues=[ReviewIssue(target="Experience", problem="vague", fix="lead with the LLM work")],
    )
    second = plan(experience=[{"id": "e15", "bullets": [{"id": "b4", "text": GOOD_B4}]}])
    llm = ScriptedLLM([plan(), second], [weak, ResumeReview(score=96, ready=True)])
    result = await run(llm)
    assert result.review is not None and result.review["passed"] and result.review["rounds"] == 2
    assert "lead with the LLM work" in llm.prompts[2].user
    assert GOOD_B4 in tex_versions.plain(result.tex) and result.doc is not None and result.doc.composed


async def test_the_loop_stops_when_a_round_is_no_better(fake_build: list[str]) -> None:
    """A round that doesn't beat the best ends the loop; the best is kept."""
    llm = ScriptedLLM(
        [plan(), plan(), plan()], [ResumeReview(score=88, ready=False), ResumeReview(score=70, ready=False)]
    )
    result = await run(llm)
    assert [r["score"] for r in result.rounds] == [88, 70] and result.review["score"] == 88  # type: ignore[index]
    assert review.summary(result.review).endswith("95 not reached after 2 rounds")


async def test_a_failed_first_composer_call_raises_so_the_card_uses_the_base(fake_build: list[str]) -> None:
    """The caller falls back to the unchanged base resume."""
    with pytest.raises(LLMInvalidOutput):
        await run(ScriptedLLM([LLMInvalidOutput("bad")], []))
    base = await tailor.unchanged(doc())
    assert base.tex == tex_versions.base_tex(doc()) and base.applied == [] and base.verify["ok"]


async def test_a_resume_over_two_pages_keeps_everything(
    fake_build: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Over the page target nothing is cut: the round still passes, with a note."""
    full = compose.compose(doc(), POOL, plan()).doc
    lines = "Asha Rao\n" + tex_versions.plain(render_tex(full)[render_tex(full).find("\\begin{document}") :])
    monkeypatch.setitem(PAGE_LINES, "lines", lines.count("\n") // 2)  # three fake pages
    result = await run(ScriptedLLM([plan()], [ResumeReview(score=97, ready=True)]))
    assert (
        result.verify["ok"] and result.verify["pages"] == 3 and "nothing was cut" in result.verify["notes"][0]
    )
    text = tex_versions.plain(result.tex)
    assert "Design Tools: AutoCad" in text and "Quality Lead" in text and "JobPilot" in text


def test_the_verifier_checks_pages_order_glyphs_and_that_changes_landed() -> None:
    """The verifier catches missing headings, extra pages, bad glyphs and lost changes."""
    base = tex_versions.base_tex(doc())
    text = "Asha Rao\nCareer Objective\nEducation\nTechnical Skills\nProjects\nExperience\nBuilt reports"
    assert verifier.check_text(
        text, 1, base, [{"section": "Projects", "after": "Built reports"}], name="Asha Rao", max_pages=1
    ).ok
    report = verifier.check_text(
        text.replace("Education\n", "") + "\n\\textbf oops �",
        2,
        base,
        [{"section": "Projects", "after": "Led a team of nine"}],
        name="Asha Rao",
        max_pages=1,
    )
    assert not report.ok and report.missing == ["Education"] and report.not_landed
    assert any("2 pages" in p for p in report.problems) and report.broken


# ---- the job's language (same skill, the job's name) ------------------------------------------------------

JD = "We want Generative AI experience, LLM evaluation, agentic AI, vector databases and Kubernetes."
TERMS = fidelity.job_terms(JD)


@pytest.mark.parametrize(
    ("new", "source"),
    [
        (
            "Worked in Generative AI projects training large language models through RLHF and SFT.",
            "Worked in Gen AI projects training large language models (LLMs) through RLHF and SFT.",
        ),
        (
            "Worked as Quality Lead on a project that developed AI agents for LLM evaluation, using an LLM as a judge.",
            "Worked as a Quality Lead on a project where we developed AI agents that use an LLM as a judge.",
        ),
        (
            "Deployed a dashboard with Supabase and vector databases (Qdrant), backed by 290+ tests.",
            "Deployed a dashboard with Supabase and Qdrant, backed by 290+ automated tests.",
        ),
    ],
)
def test_a_bullet_may_use_the_jobs_name_for_a_skill_it_already_names(new: str, source: str) -> None:
    """'Gen AI' may become the job's 'Generative AI': the taxonomy says it is the same skill."""
    assert fidelity.rewrite_problem(new, source) is not None  # without the job's terms: refused
    assert fidelity.rewrite_problem(new, source, terms=TERMS) is None


def test_the_jobs_words_never_add_a_skill_the_fact_lacks() -> None:
    """A job skill the fact doesn't name stays out, even when the job asks for it."""
    source = "Worked in Gen AI projects training large language models (LLMs) through RLHF and SFT."
    problem = fidelity.rewrite_problem(
        "Worked in Gen AI projects training LLMs on Kubernetes through RLHF and SFT.", source, terms=TERMS
    )
    assert problem is not None and "Kubernetes" in problem
    problem = fidelity.rewrite_problem(
        "Worked in Gen AI projects training LLMs through RLHF and SFT with agentic AI.", source, terms=TERMS
    )
    assert problem is not None  # 'agentic AI' is a job skill, but not one this fact names


def test_the_composer_applies_the_jobs_names() -> None:
    """With the JD passed in, the composer keeps a bullet reworded into the job's terms."""
    reworded = "Worked in Generative AI projects focused on training large language models (LLMs) through RLHF and SFT tasks."
    p = plan(experience=[{"id": "e15", "bullets": [{"id": "b4", "text": reworded}]}])
    assert entry_of(compose.compose(doc(), POOL, p).doc, "e15").bullets[0].text == B4  # no JD: original
    assert entry_of(compose.compose(doc(), POOL, p, JD).doc, "e15").bullets[0].text == reworded


def test_a_stray_character_from_the_model_is_refused_not_fatal() -> None:
    """A reworded bullet with a character the fonts can't print (live: '撇') keeps the original."""
    bad = plan(
        experience=[{"id": "e15", "bullets": [{"id": "b4", "text": B4.replace("projects", "projects撇")}]}]
    )
    out = compose.compose(doc(), POOL, bad)
    assert entry_of(out.doc, "e15").bullets[0].text == B4 and any("characters" in r for r in out.rejected)
    assert "撇" not in render_tex(out.doc)
    assert fidelity.summary_problem(
        "Data Scientist at Turing 撇 working on Gen AI projects that train large language models through RLHF.",
        FACT_TEXTS,
        SUMMARY,
        ["Data Scientist"],
    )


# ---- two-line bullets (decision 38) -----------------------------------------------------------------------

LONG = (
    "Worked in the R&D team, researching various benchmarks and evaluating LLM models based on their "
    "performance across these benchmarks. Researched ways to improve model performance by identifying the "
    "types of data required for better training and evaluation. Worked closely with the Frontier Models "
    "Research team to understand how models need to be trained and what types of data can be used."
)


def test_a_fact_longer_than_two_lines_is_split_by_sentence() -> None:
    """The R&D fact becomes bullets of whole sentences, each within two lines, same entry and evidence."""
    rows = [*ROWS, row(44, "achievement", LONG, parent_id=40)]
    turing = pool.build(doc(), rows, max_chars=210).entry("e15")
    assert turing is not None
    pieces = [b for b in turing.bullets if b.id.startswith("p44")]
    assert [b.id for b in pieces] == ["p44", "p44.2", "p44.3"]
    assert all(len(b.text) <= 210 and b.evidence == [44, 40] for b in pieces)
    assert " ".join(b.text for b in pieces) == LONG  # nothing reworded or lost


def test_a_rewording_may_not_run_past_two_lines() -> None:
    """A faithful rewording that is too long keeps the original."""
    small = pool.build(doc(), ROWS, max_chars=90)
    long_b4 = (
        "Worked in Gen AI projects focused on training large language models (LLMs) through RLHF and SFT."
    )
    out = compose.compose(
        doc(), small, plan(experience=[{"id": "e15", "bullets": [{"id": "b4", "text": long_b4}]}])
    )
    assert entry_of(out.doc, "e15").bullets[0].text == B4 and any("two lines" in r for r in out.rejected)


def test_a_too_long_single_sentence_is_flagged_for_shortening() -> None:
    """The composer is told which bullets are over two lines."""
    prompt = pool.as_prompt(pool.build(doc(), ROWS, max_chars=60))
    turing = next(e for e in prompt["experience"] if e["id"] == "e15")
    assert "too_long" in turing["bullets"][0]


def test_a_shortening_that_fits_two_lines_is_used() -> None:
    """A too-long bullet shortened with its own words to fit is applied."""
    small = pool.build(doc(), ROWS, max_chars=100)
    shorter = "Worked in Gen AI projects training large language models (LLMs) through RLHF and SFT."
    out = compose.compose(
        doc(), small, plan(experience=[{"id": "e15", "bullets": [{"id": "b4", "text": shorter}]}])
    )
    assert entry_of(out.doc, "e15").bullets[0].text == shorter


def test_a_skill_listed_on_two_base_lines_stays_on_both() -> None:
    """Python on 'Data' and 'Languages' in the base resume stays on both, whatever the plan does."""
    base = doc()
    skills = next(s for s in base.sections if s.layout == Layout.SKILLS)
    skills.entries.append(Entry(id="sk3", title="Data", items=["Python", "Excel"], evidence=[20]))
    p = plan(skills=[{"label": "Languages", "items": ["SQL"]}, {"label": "Data", "items": ["Excel"]}])
    out = compose.compose(base, pool.build(base, ROWS), p)
    lines = {e.title: e.items for s in out.doc.sections if s.layout == Layout.SKILLS for e in s.entries}
    assert lines["Languages"] == ["SQL", "Python"] and lines["Data"] == ["Excel", "Python"]
    assert lines["Design Tools"] == ["AutoCad"]  # left out by the plan: kept whole


# ---- decision 41: second try, objective from facts, job-only skills, the title's skill names ---------------


async def test_a_refused_bullet_gets_a_second_try(fake_build: list[str]) -> None:
    """The first rewording adds 'ensuring'; the focused second try is faithful and lands."""
    first = plan(
        experience=[
            {
                "id": "e15",
                "bullets": [
                    {
                        "id": "b4",
                        "text": "Worked in Gen AI projects ensuring large language models (LLMs) training through RLHF and SFT tasks.",
                    }
                ],
            }
        ]
    )
    llm = ScriptedLLM([first], [ResumeReview(score=97, ready=True)])
    llm.fixes = [BulletFixes(bullets=[{"id": "b4", "text": GOOD_B4}])]
    result = await run(llm)
    assert GOOD_B4 in tex_versions.plain(result.tex)
    assert any(a["op"] == "second_try" for a in result.applied)
    sent = next(p for p in llm.prompts if "Bullets to fix" in p.user)
    assert "ensuring" in sent.user and "adds words" in sent.user  # told what was refused and why


def test_a_refused_objective_is_written_from_profile_facts() -> None:
    """'Generative AI Developer...' is refused; a true objective from the role, project and degree is used."""
    rows = [
        *ROWS,
        row(70, "education", "B.Tech, IIT Palakkad", qualification="B.Tech", institution="IIT Palakkad"),
    ]
    rows = [r if r["id"] != 60 else row(60, "project", "JobPilot", name="JobPilot", tech=["LangGraph"],
            about="Developed an AI assistant that finds and scores jobs.") for r in rows]  # fmt: skip
    p = pool.build(doc(), rows)
    bad = plan(
        summary="Generative AI Developer with strong expertise in LLMs, Python and SQL for Gen AI projects and pipelines."
    )
    out = compose.compose(doc(), p, bad, "We need Python and LangGraph", required=["Python", "LangGraph"])
    text = entry_of(out.doc, "e0").text
    assert text == (
        "Data Scientist at Turing with hands-on experience in Python and LangGraph. Built JobPilot, "
        "an AI assistant that finds and scores jobs. B.Tech graduate from IIT Palakkad."
    )
    assert "Developer" not in text and any("from your profile facts" in a["reason"] for a in out.applied)


def test_only_the_skills_the_job_asks_for_are_shown() -> None:
    """With a JD, skills it doesn't ask for are left out; an unplaced skill joins a line of its kind."""
    base = doc()
    skills = next(s for s in base.sections if s.layout == Layout.SKILLS)
    skills.entries.append(Entry(id="sk9", title="AI Tools", items=["Machine Learning"], evidence=[]))
    out = compose.compose(base, pool.build(base, ROWS), plan(), "Python, SQL, LangGraph and agentic AI",
                          required=["Python", "LangGraph"])  # fmt: skip
    lines = {e.title: e.items for s in out.doc.sections if s.layout == Layout.SKILLS for e in s.entries}
    assert lines == {"Languages": ["Python", "SQL"], "AI Tools": ["LangGraph", "Agentic AI"]}
    assert "AutoCad" not in tex_versions.plain(render_tex(out.doc))  # not asked for: not shown
    assert any(a["op"] == "job_skills" for a in out.applied)


def test_without_a_job_description_every_skill_stays() -> None:
    """No JD (or almost no matches): every skill is shown rather than a near-empty section."""
    out = compose.compose(doc(), POOL, plan(), "", required=[])
    shown = [i for s in out.doc.sections if s.layout == Layout.SKILLS for e in s.entries for i in e.items]
    assert {"Python", "SQL", "AutoCad", "LangGraph"} <= set(shown)


def test_the_job_title_counts_as_the_jobs_language() -> None:
    """'Generative AI' only in the title still lets 'Gen AI' become 'Generative AI' in a bullet."""
    title_only = "Senior Automation Engineer - Generative AI\nBuild automation with Python."
    reworded = "Worked in Generative AI projects focused on training large language models (LLMs) through RLHF and SFT tasks."
    p = plan(experience=[{"id": "e15", "bullets": [{"id": "b4", "text": reworded}]}])
    assert entry_of(compose.compose(doc(), POOL, p, title_only).doc, "e15").bullets[0].text == reworded
