"""The extraction eval's scoring, with a fake extractor (the live run needs an API key)."""

from typing import Any

from evals.runners import extraction
from jobpilot.models.requirements import Requirements, Seniority


async def test_perfect_extractor_passes_every_gate() -> None:
    """A perfect extractor passes every extraction gate."""

    async def perfect(case: dict[str, Any]) -> Requirements:
        """Return exactly the labelled requirements."""
        return Requirements(
            must_skills=case["must"],
            nice_skills=case["nice"],
            years_min=case.get("years_min"),
            years_max=case.get("years_max"),
            seniority=Seniority.UNKNOWN,
            domain="",
            title_norm="",
            is_job_description=case.get("is_job_description", True),
        )

    summary, rows, failures = await extraction.run(perfect)
    assert failures == [] and summary["must_recall"] == 1.0 and len(rows) == 10


async def test_lazy_extractor_fails_recall() -> None:
    """An extractor that finds one skill per JD fails recall."""

    async def lazy(case: dict[str, Any]) -> Requirements:
        """Return only the first labelled skill."""
        return Requirements(
            must_skills=case["must"][:1],
            nice_skills=[],
            years_min=None,
            years_max=None,
            seniority=Seniority.UNKNOWN,
            domain="",
            title_norm="",
            is_job_description=True,
        )

    summary, _, failures = await extraction.run(lazy)
    assert summary["must_recall"] < 0.5 and any("must_recall" in f for f in failures)
