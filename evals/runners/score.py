"""Score evaluation: agreement between the code-computed score and your decisions.

Labels come from your own taps: a card whose resume you approved or applied to = relevant; a card you skipped
= not relevant. Reports precision among cards above the threshold and agreement (score >= threshold vs your
decision) with a 95% bootstrap confidence interval. Gate: agreement >= 0.8, starting from 50 labelled cards.
Usage: uv run python -m evals.runners.score
"""

from __future__ import annotations

import asyncio
import json
import random

from sqlalchemy import select

from jobpilot.db.session import dispose_engine, transaction
from jobpilot.db.tables import cards, scores
from jobpilot.profile import preferences

GATE, MIN_LABELS = 0.8, 50


def agreement_ci(
    pairs: list[tuple[bool, bool]], rounds: int = 2000, seed: int = 7
) -> tuple[float, float, float]:
    """Return agreement and its 95% bootstrap confidence interval.

    Args:
        pairs: (score above threshold, user found it relevant) pairs.
        rounds: Bootstrap resamples.
        seed: Random seed, so the interval is reproducible.

    Returns:
        (agreement, lower bound, upper bound).
    """
    rng = random.Random(seed)
    value = sum(p == y for p, y in pairs) / len(pairs)
    samples = sorted(
        sum(p == y for p, y in (rng.choice(pairs) for _ in pairs)) / len(pairs) for _ in range(rounds)
    )
    return value, samples[int(0.025 * rounds)], samples[int(0.975 * rounds)]


async def main() -> int:
    """Compare scores with the user's own decisions on cards.

    Approved, applied or emailed cards count as relevant; skipped ones
    as not relevant.

    Returns:
        The exit code: 0 at the gate, 1 below, 2 with too few labels.
    """
    async with transaction() as conn:
        prefs, _ = await preferences.load(conn)
        rows = (await conn.execute(select(cards.c.job_id, cards.c.status))).all()
        latest = {
            r[0]: float(r[1])
            for r in (await conn.execute(select(scores.c.job_id, scores.c.score).order_by(scores.c.id))).all()
        }
    labelled = [
        (latest[job_id] >= prefs.threshold, status != "skipped")
        for job_id, status in rows
        if job_id in latest and status in ("skipped", "resume_approved", "applied", "sending", "emailed")
    ]
    await dispose_engine()
    if len(labelled) < MIN_LABELS:
        print(
            json.dumps(
                {"labelled": len(labelled), "needed": MIN_LABELS, "result": "not enough decisions yet"}
            )
        )
        return 2
    value, lo, hi = agreement_ci(labelled)
    predicted = [y for p, y in labelled if p]
    precision = sum(predicted) / len(predicted) if predicted else None
    print(
        json.dumps({"labelled": len(labelled), "agreement": value, "ci95": [lo, hi], "precision": precision})
    )
    return 0 if value >= GATE else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
