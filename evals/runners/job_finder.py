"""Job Finder eval scoring plus the credential-free SSRF/deny-list gate."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from typing import Any

import yaml

from jobpilot.core.config import REPO_ROOT, get_settings
from jobpilot.servers.web.ssrf_guard import UnsafeUrl, validate_url

DATASET = REPO_ROOT / "evals" / "datasets" / "job_finder" / "labelled.jsonl"
Predictor = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def load_cases() -> list[dict[str, Any]]:
    """Load the labelled Job Finder cases."""
    return [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]


async def fixture_resolver(host: str, port: int) -> list[str]:
    """Resolve every host to one public address (no network in the eval)."""
    return ["93.184.216.34"]


async def security_rows(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check that every blocked-URL case is refused by the fetch guard."""
    rows = []
    for case in cases:
        if case["kind"] != "blocked_url":
            continue
        blocked = False
        try:
            await validate_url(case["url"], resolver=fixture_resolver)
        except UnsafeUrl:
            blocked = True
        rows.append({"id": case["id"], "blocked": blocked})
    return rows


async def run(predictor: Predictor) -> tuple[dict[str, float], list[dict[str, Any]], list[str]]:
    """Score a predictor on leads, JD finding and wrong matches, plus the security gate.

    Args:
        predictor: Returns the agent's answer for one case.

    Returns:
        (summary, per-case rows, gate failures).
    """
    cases = load_cases()
    rows: list[dict[str, Any]] = []
    expected_leads = found_leads = 0
    jd_total = jd_correct = wrong_matches = 0
    for case in cases:
        if case["kind"] == "blocked_url":
            continue
        got = await predictor(case)
        if case["kind"] == "lead":
            expected = set(case["expected_urls"])
            found = set(got.get("urls") or [])
            expected_leads += len(expected)
            found_leads += len(expected & found)
            rows.append(
                {"id": case["id"], "found": sorted(expected & found), "missed": sorted(expected - found)}
            )
        elif case["kind"] == "jd":
            jd_total += 1
            ok = got.get("url") == case["expected_url"]
            jd_correct += int(ok)
            rows.append({"id": case["id"], "correct": ok, "url": got.get("url")})
        else:
            wrong = bool(got.get("url"))
            wrong_matches += int(wrong)
            rows.append({"id": case["id"], "wrong_match": wrong, "url": got.get("url")})
    security = await security_rows(cases)
    rows.extend(security)
    summary = {
        "lead_recall": round(found_leads / expected_leads, 3) if expected_leads else 1.0,
        "jd_accuracy": round(jd_correct / jd_total, 3) if jd_total else 1.0,
        "wrong_job_matches": float(wrong_matches),
        "blocked_fetch_failures": float(sum(not row["blocked"] for row in security)),
    }
    gates = yaml.safe_load((REPO_ROOT / "evals" / "thresholds.yaml").read_text())["job_finder"]
    failures = []
    for key in ("lead_recall", "jd_accuracy"):
        if summary[key] < gates[key]:
            failures.append(f"{key} {summary[key]} < {gates[key]}")
    for key in ("wrong_job_matches", "blocked_fetch_failures"):
        if summary[key] > gates[key]:
            failures.append(f"{key} {summary[key]} > {gates[key]}")
    return summary, rows, failures


async def main() -> int:
    """Run the security gate; agent quality needs live keys.

    Returns:
        The exit code (1 if any blocked URL was allowed).
    """
    cases = load_cases()
    security = await security_rows(cases)
    if any(not row["blocked"] for row in security):
        print("FAIL: an SSRF or deny-list URL was allowed")
        return 1
    # Web search runs on the OpenAI key too (decision 21), so it is the only key needed.
    key = get_settings().openai_api_key
    if key is None or not key.get_secret_value():
        print(
            f"Security gate PASS ({len(security)} blocked). Agent quality skipped until "
            "OPENAI_API_KEY is added."
        )
        return 0
    print("Live agent eval is run through the worker's MCP connections during the final credentialed check.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
