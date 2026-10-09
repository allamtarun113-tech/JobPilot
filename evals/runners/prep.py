"""Prep evals: offline citation-boundary checks and an opt-in held-out quality eval."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable

from jobpilot.core.config import REPO_ROOT
from jobpilot.models.prep import PackQuestion, PackQuestions
from jobpilot.prep import qa_bank, qa_generator

DATASET = REPO_ROOT / "evals" / "datasets" / "prep" / "held_out.json"
Predictor = Callable[[dict], Awaitable[PackQuestions]]


def run() -> tuple[dict[str, float], list[str]]:
    """Check that only valid citations mark a prep answer verified.

    Returns:
        (the pass rate, the failures).
    """
    failures = []
    for citation, expected in (("chunk:1", True), ("chunk:other", False), ("", False)):
        question = PackQuestion(
            group="skill",
            about="SQL",
            category="technical",
            question="Question",
            answer="Answer",
            citations=[citation] if citation else [],
        )
        labels = {"chunk:1": "Book, p. 1"}
        [entry] = qa_bank.from_pack([question], {"id": 1}, labels, set(labels))
        if entry["verified"] != expected or (expected and entry["source_labels"] != ["Book, p. 1"]):
            failures.append(f"citation boundary failed: {citation}")
    return {"citation_boundary_pass_rate": (3 - len(failures)) / 3}, failures


async def quality(predict: Predictor) -> tuple[dict[str, float], list[str]]:
    """Score generated answers on held-out cases.

    Each case passes only if every answer cites its source, states each
    required fact and contradicts nothing.

    Args:
        predict: Generates the questions for one case.

    Returns:
        (the pass rate, the ids of failing cases).
    """
    cases = json.loads(DATASET.read_text())
    failures = []
    for case in cases:
        result = await predict(case)
        # All generated answers must use the supplied source, and every required fact must appear.
        text = " ".join(question.answer.lower() for question in result.questions)
        cited = all(question.citations == [f"chunk:{case['id']}"] for question in result.questions)
        supported = all(any(option in text for option in alternatives) for alternatives in case["required"])
        contradicted = any(term in text for term in case["forbidden"])
        if not cited or not supported or contradicted:
            failures.append(case["id"])
    return {"held_out_pass_rate": (len(cases) - len(failures)) / len(cases)}, failures


async def live() -> int:
    """Run the held-out quality eval against the real model (needs a key)."""
    from jobpilot.core.config import get_settings
    from jobpilot.db.session import dispose_engine
    from jobpilot.llm.client import LLMClient

    if not get_settings().openai_api_key:
        print("Live prep evaluation needs OPENAI_API_KEY; no model quality result is available.")
        return 2
    llm = LLMClient()

    async def predict(case: dict) -> PackQuestions:
        """Generate Q&A for one case from its single source section."""
        result = await qa_generator.generate_missing(
            llm,
            wanted={"skill": [case["topic"]]},
            job={"title": "evaluation", "company": "evaluation"},
            profile={},
            sources=[
                {
                    "citation": f"chunk:{case['id']}",
                    "source": "Held-out synthetic source",
                    "page_start": 1,
                    "topic": case["topic"],
                    "text": case["source"],
                }
            ],
            research=[],
        )
        return result.output

    try:
        summary, failures = await quality(predict)
        print(json.dumps({"mode": "live_model_quality", **summary, "failures": failures}))
        return 1 if failures else 0
    finally:
        await dispose_engine()


def main() -> int:
    """Run the offline check, or the live eval with --live; return the exit code."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live", action="store_true", help="Use credentials and evaluate actual model outputs"
    )
    if parser.parse_args().live:
        return asyncio.run(live())
    summary, failures = run()
    print(
        json.dumps(
            {
                "mode": "offline_citation_boundaries",
                **summary,
                "failures": failures,
                "model_quality": "not evaluated; run --live after adding keys",
            }
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
