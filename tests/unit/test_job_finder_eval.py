"""Job Finder labelled eval scoring and mandatory security gate."""

from typing import Any

from evals.runners import job_finder


async def test_perfect_job_finder_passes_all_gates() -> None:
    """Perfect answers pass every Job Finder gate."""

    async def perfect(case: dict[str, Any]) -> dict[str, Any]:
        """Return the labelled answer for each case."""
        if case["kind"] == "lead":
            return {"urls": case["expected_urls"]}
        if case["kind"] == "jd":
            return {"url": case["expected_url"]}
        return {}

    summary, rows, failures = await job_finder.run(perfect)
    assert failures == []
    assert summary == {
        "lead_recall": 1.0,
        "jd_accuracy": 1.0,
        "wrong_job_matches": 0.0,
        "blocked_fetch_failures": 0.0,
    }
    assert len(rows) == len(job_finder.load_cases())


async def test_wrong_matches_and_misses_fail_quality_gates() -> None:
    """Wrong jobs and missed leads fail the quality gates."""

    async def bad(case: dict[str, Any]) -> dict[str, Any]:
        """Return a wrong job and no leads."""
        return {"url": "https://wrong.example/job", "urls": []}

    summary, _rows, failures = await job_finder.run(bad)
    assert summary["lead_recall"] == 0 and summary["jd_accuracy"] == 0
    assert summary["wrong_job_matches"] > 0
    assert {failure.split()[0] for failure in failures} >= {
        "lead_recall",
        "jd_accuracy",
        "wrong_job_matches",
    }


async def test_main_runs_the_security_gate_without_an_api_key(capsys) -> None:  # type: ignore[no-untyped-def]
    """Without OPENAI_API_KEY the entry point passes the security gate and skips agent quality."""
    assert await job_finder.main() == 0
    assert "Security gate PASS" in capsys.readouterr().out
