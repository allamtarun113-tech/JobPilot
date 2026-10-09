"""The code-computed score (rubric v3).

Six dimensions, exact gap points, weight hand-over for unused
dimensions, the "why you fit" text, ranking by learnability,
determinism.
"""

import pytest

from jobpilot.integrations.vectors import VectorStore
from jobpilot.llm.embeddings import HashEmbedder
from jobpilot.llm.schema import strict_schema
from jobpilot.matching import embed_score
from jobpilot.matching import gap as gap_mod
from jobpilot.matching.compute import DIMENSIONS, compute
from jobpilot.matching.profile_view import ProfileView, months_between
from jobpilot.models.requirements import Requirements, Seniority
from jobpilot.models.score import GapItem


def view() -> ProfileView:
    """Return a sample profile: five skills, 1.5 years and one retail project."""
    v = ProfileView(
        skill_ids={"python": [1], "sql": [2], "tableau": [3], "pandas": [4], "excel": [5]},
        skill_names={"python": [1], "sql": [2], "tableau": [3], "pandas": [4], "excel": [5]},
        skill_texts={1: "Python", 2: "SQL", 3: "Tableau", 4: "Pandas", 5: "Excel"},
        years=1.5,
        role_titles=["Data Analyst"],
        confirmed_count=6,
        hash="h1",
        context_items={10: "Built retail sales dashboards in Tableau for store managers"},
    )
    v.summary_text = "Data analyst. Python SQL Tableau. retail dashboards"
    return v


def req(**kw: object) -> Requirements:
    """Return sample requirements, overridden by ``kw``."""
    base: dict[str, object] = dict(
        technologies=["Python", "SQL", "Tableau", "Excel", "Kafka"],
        nice_skills=["dbt"],
        years_min=8,
        years_max=None,
        seniority=Seniority.SENIOR,
        domain="retail analytics dashboards",
        title_norm="data analyst",
        is_job_description=True,
    )
    return Requirements.model_validate(base | kw)


@pytest.fixture
async def vectors() -> VectorStore:
    """Return an in-memory store with the sample profile indexed."""
    vs = VectorStore.in_memory()
    await embed_score.sync_profile(view(), HashEmbedder(), vs)
    return vs


async def score(r: Requirements, vectors: VectorStore, **kw: object):  # type: ignore[no-untyped-def]
    """Score requirements against the sample profile."""
    return await compute(r, view(), embedder=HashEmbedder(), vectors=vectors, **kw)  # type: ignore[arg-type]


async def test_only_the_new_dimensions_are_scored(vectors: VectorStore) -> None:
    """Only the six rubric dimensions are scored; experience isn't."""
    result = await score(req(), vectors)
    assert tuple(result.parts) == DIMENSIONS
    assert "experience" not in result.parts and "role" not in result.parts
    # 8+ years asked of someone with 1.5 costs nothing: experience is not scored.
    assert result.score == round(sum(result.parts.values()), 1)


async def test_unused_dimensions_hand_their_weight_over_and_gap_points_are_exact(
    vectors: VectorStore,
) -> None:
    """Unused dimensions pass their weight on; gap points are exact."""
    result = await score(req(), vectors)  # only technologies listed: they carry 30 + 20 + 10 = 60
    assert result.parts["technologies"] == 48.0  # 4 of 5: 60 * 4/5
    assert result.parts["skills"] == 0.0 and result.parts["concepts"] == 0.0
    assert [(g.skill, g.kind, g.points) for g in result.gap] == [
        ("Kafka", "technology", 12.0),
        ("dbt", "nice", 10.0),
    ]
    assert {m.skill for m in result.matched} == {"Python", "SQL", "Tableau", "Excel"}
    assert all(m.evidence for m in result.matched)


async def test_each_list_scores_its_own_dimension(vectors: VectorStore) -> None:
    """Technologies, skills and concepts each score their own dimension."""
    r = req(technologies=["Python", "Kafka"], skills=["stakeholder management"], concepts=["dashboards"])
    result = await score(r, vectors)
    assert result.parts["technologies"] == 15.0  # 30 * 1/2
    assert result.parts["skills"] == 0.0  # 20, nothing found
    assert result.parts["concepts"] == 10.0  # "dashboards" is in your confirmed project
    points = {g.skill: (g.kind, g.points) for g in result.gap}
    assert points["stakeholder management"] == ("skill", 20.0) and points["Kafka"] == ("technology", 15.0)
    assert any(w.startswith("dashboards: used in Built retail sales dashboards") for w in result.fit.why)


async def test_rubric_v1_rows_count_must_skills_as_technologies(vectors: VectorStore) -> None:
    """Old extractions score must_skills as technologies."""
    old = Requirements.model_validate(
        dict(must_skills=["Python", "Kafka"], nice_skills=[], years_min=None, years_max=None,
             seniority="entry", domain="retail", title_norm="analyst", is_job_description=True)
    )  # fmt: skip
    result = await score(old, vectors)
    assert result.parts["technologies"] == 30.0  # 60 * 1/2


def test_must_skills_is_derived_and_never_asked_of_the_model() -> None:
    """must_skills is filled by code and kept out of the model's schema."""
    r = req(skills=["data analysis"])
    assert r.must_skills == ["Python", "SQL", "Tableau", "Excel", "Kafka", "data analysis"]
    assert "must_skills" not in strict_schema(Requirements)["properties"]


async def test_snippet_without_lists_earns_half(vectors: VectorStore) -> None:
    """A snippet that lists nothing earns half, not full, points."""
    empty = req(technologies=[], nice_skills=[])
    full, snippet = await score(empty, vectors), await score(empty, vectors, jd_is_snippet=True)
    assert full.parts["technologies"] == 30.0 and snippet.parts["technologies"] == 15.0
    assert snippet.parts["nice"] == 5.0 and not full.gap


async def test_same_input_same_score(vectors: VectorStore) -> None:
    """The same JD and profile always give the same result."""
    a, b = await score(req(), vectors), await score(req(), vectors)
    assert a.model_dump() == b.model_dump()


async def test_fit_explains_strengths_and_gaps(vectors: VectorStore) -> None:
    """The fit text names the strengths and the top missing skill."""
    result = await score(req(), vectors)
    assert result.fit.summary.startswith("Strong on Python, SQL, Tableau; missing Kafka")
    assert result.fit.why[0] == "Python: in your skills"
    assert result.fit.missing[0] == "Kafka (+12)"


def test_skills_to_learn_rank_by_points_and_learnability() -> None:
    """Easy skills worth about the same rank first."""
    hard = GapItem(skill="Kubernetes", skill_id="kubernetes", kind="technology", points=10, learnability=0.1)
    easy = GapItem(skill="Pandas", skill_id="pandas", kind="technology", points=9, learnability=0.9)
    small = GapItem(skill="dbt", skill_id=None, kind="nice", points=2, learnability=1.0)
    assert [g.skill for g in gap_mod.rank([hard, small, easy])] == ["Pandas", "Kubernetes", "dbt"]


def test_months_between() -> None:
    """Date ranges convert to months; nonsense gives 0."""
    assert months_between("may 2023–june 2023") == 2
    assert months_between("2020–2024") == 49
    assert months_between("garbage") == 0


class TableEmbedder:
    """2-d vectors: your skills point one way, your project another; a JD phrase is set per test."""

    dim = 2

    def __init__(self, phrases: dict[str, list[float]]) -> None:
        """Use fixed vectors for the given phrases."""
        self.phrases = phrases

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return the fixed vector for each text."""
        skills = set(view().skill_texts.values())
        return [
            self.phrases.get(
                t, [0.0, 1.0] if t in skills else [1.0, 0.0] if "retail" in t.lower() else [-1.0, 0.0]
            )
            for t in texts
        ]


async def test_abilities_earn_partial_credit_by_meaning_and_the_gap_keeps_the_rest() -> None:
    """Skills earn partial credit by meaning; technologies never do."""
    embedder = TableEmbedder({"model deployment": [0.8, 0.6]})  # 0.80 to your project: half credit
    vectors = VectorStore.in_memory(dim=2)
    await embed_score.sync_profile(view(), embedder, vectors)  # type: ignore[arg-type]
    r = req(technologies=["Python"], skills=["model deployment"], nice_skills=[])
    result = await compute(r, view(), embedder=embedder, vectors=vectors)  # type: ignore[arg-type]
    # Only technologies and skills are listed: they share 60 points as 30:20 -> 36 and 24.
    assert result.parts["technologies"] == 36.0 and result.parts["skills"] == 12.0
    assert [(g.skill, g.kind, g.points) for g in result.gap] == [("model deployment", "skill", 12.0)]
    similar = next(m for m in result.matched if m.skill == "model deployment")
    assert similar.how == "similar" and similar.evidence == [10]
    # Technologies never get partial credit: a named tool is either yours or not.
    tech = await compute(
        req(technologies=["model deployment"], nice_skills=[]), view(), embedder=embedder, vectors=vectors
    )  # type: ignore[arg-type]
    assert tech.parts["technologies"] == 0.0


async def test_a_snippet_never_hands_weight_to_one_vague_phrase(vectors: VectorStore) -> None:
    """On a snippet each dimension keeps half its own weight."""
    r = req(technologies=[], skills=["implementing machine learning solutions"], nice_skills=[])
    full = await score(r, vectors)
    snippet = await score(r, vectors, jd_is_snippet=True)
    assert full.parts["technologies"] == 0.0  # full JD: its weight went to skills
    assert snippet.parts["technologies"] == 15.0 and snippet.parts["concepts"] == 5.0  # half their own weight
    assert snippet.gap[0].points == 20.0  # the phrase is worth only its own dimension
