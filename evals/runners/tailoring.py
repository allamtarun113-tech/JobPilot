"""Credential-free resume claim guardrail eval.

Live model quality runs after keys are added.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import yaml

from jobpilot.core.config import REPO_ROOT, get_settings
from jobpilot.safety.citations import supported_numbers

DATASET = REPO_ROOT / "evals" / "datasets" / "tailoring" / "starter.jsonl"


def load_cases() -> list[dict[str, Any]]:
    """Load the tailoring claim cases."""
    return [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]


def accepted(case: dict[str, Any]) -> bool:
    """Return True if the claim checks would accept a case's rewrite."""
    facts = {int(key): value for key, value in case["facts"].items()}
    cited = [facts[item_id] for item_id in case["evidence"] if item_id in facts]
    return len(cited) == len(set(case["evidence"])) and supported_numbers(case["text"], cited)


def run() -> tuple[dict[str, int], list[dict[str, Any]], list[str]]:
    """Run every case and check that no uncited claim was accepted.

    Returns:
        (summary counts, per-case rows, gate failures).
    """
    rows = []
    uncited = false_rejects = 0
    for case in load_cases():
        got = accepted(case)
        uncited += int(got and not case["allowed"])
        false_rejects += int(not got and case["allowed"])
        rows.append({"id": case["id"], "accepted": got, "expected": case["allowed"]})
    summary = {"uncited_claims_accepted": uncited, "supported_claims_rejected": false_rejects}
    gates = yaml.safe_load((REPO_ROOT / "evals" / "thresholds.yaml").read_text())["tailoring"]
    failures = [f"{key} {summary[key]} > {limit}" for key, limit in gates.items() if summary[key] > limit]
    return summary, rows, failures


def main() -> int:
    """Run the eval and print the result; return the exit code."""
    summary, _rows, failures = run()
    suffix = (
        " Live model quality deferred until OPENAI_API_KEY is added."
        if not get_settings().openai_api_key
        else ""
    )
    print(json.dumps(summary), "PASS" if not failures else "FAIL: " + "; ".join(failures), suffix)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
