"""Requirement extraction eval: must-have precision and recall, years accuracy, JD detection.

Usage: uv run python -m evals.runners.extraction   (needs OPENAI_API_KEY; uses the pinned model + prompt)
Writes evals/reports/extraction-<timestamp>.json and exits non-zero if a gate fails.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from jobpilot.core.config import REPO_ROOT, get_settings
from jobpilot.models.requirements import Requirements
from jobpilot.reference import catalog

DATASETS = REPO_ROOT / "evals" / "datasets" / "extraction"
Extractor = Callable[[dict[str, Any]], Awaitable[Requirements]]


def skill_key(name: str) -> str:
    """Return the comparison key of a skill: its taxonomy id, else its lower-cased name."""
    return catalog.skill_id(name) or name.lower().strip()


def load_cases() -> list[dict[str, Any]]:
    """Load every hand-labelled extraction case from the dataset folder."""
    cases = []
    for path in sorted(DATASETS.glob("*.jsonl")):
        cases += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return cases


def score_case(case: dict[str, Any], got: Requirements) -> dict[str, Any]:
    """Compare one extraction with its labels.

    Returns:
        True/false positives and negatives, years and JD-detection
        checks, and the missed and extra skills.
    """
    expected = {skill_key(s) for s in case["must"]}
    predicted = {skill_key(s) for s in got.must_skills}
    tp = len(expected & predicted)
    return {
        "id": case["id"],
        "tp": tp,
        "fp": len(predicted - expected),
        "fn": len(expected - predicted),
        "years_ok": got.years_min == case.get("years_min"),
        "jd_ok": got.is_job_description == case.get("is_job_description", True),
        "missed": sorted(expected - predicted),
        "extra": sorted(predicted - expected),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Return must-have precision and recall, years accuracy and JD detection accuracy."""
    tp, fp, fn = (sum(r[k] for r in rows) for k in ("tp", "fp", "fn"))
    jd_rows = [r for r in rows if r["jd_ok"] is not None]
    return {
        "must_precision": round(tp / (tp + fp), 3) if tp + fp else 1.0,
        "must_recall": round(tp / (tp + fn), 3) if tp + fn else 1.0,
        "years_accuracy": round(sum(r["years_ok"] for r in rows) / len(rows), 3) if rows else 1.0,
        "jd_detection_accuracy": round(sum(r["jd_ok"] for r in jd_rows) / len(jd_rows), 3)
        if jd_rows
        else 1.0,
    }


def gates() -> dict[str, float]:
    """Return the pass thresholds for this eval from ``evals/thresholds.yaml``."""
    return dict(yaml.safe_load((REPO_ROOT / "evals" / "thresholds.yaml").read_text())["extraction"])


async def run(extractor: Extractor) -> tuple[dict[str, float], list[dict[str, Any]], list[str]]:
    """Run the eval with an extractor and check the gates.

    Args:
        extractor: Returns Requirements for one case.

    Returns:
        (summary, per-case rows, gate failures).
    """
    rows = [score_case(case, await extractor(case)) for case in load_cases()]
    summary = summarize(rows)
    failures = [f"{k} {summary[k]} < {v}" for k, v in gates().items() if summary[k] < v]
    return summary, rows, failures


async def main() -> int:
    """Run the live extraction eval and write a report; skipped without an API key.

    Returns:
        The process exit code (1 when a gate fails).
    """
    if not get_settings().openai_api_key:
        print("Skipped: the extraction eval needs OPENAI_API_KEY (added at the end of the build).")
        return 0
    from jobpilot.llm.client import LLMClient
    from jobpilot.matching import extract
    from jobpilot.reference.normalize import jd_hash

    llm = LLMClient()

    async def extractor(case: dict[str, Any]) -> Requirements:
        """Extract one case's requirements through the cached pipeline."""
        _, req = await extract.extract(
            llm, jd_text=case["jd"], jd_hash=jd_hash(case["jd"]), title=case["title"], run_id=None
        )
        return req

    summary, rows, failures = await run(extractor)
    report = {
        "at": datetime.now(UTC).isoformat(),
        "model_prompt": extract.cache_key(),
        "summary": summary,
        "cases": rows,
        "failures": failures,
    }
    out = REPO_ROOT / "evals" / "reports" / f"extraction-{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
    await asyncio.to_thread(Path(out).write_text, json.dumps(report, indent=2))
    print(json.dumps(summary, indent=2), "\n", "PASS" if not failures else "FAIL: " + "; ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
