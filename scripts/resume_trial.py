"""Try resume v3 tailoring on real scored jobs without creating cards or messaging anyone.

    make resume-trial ARGS="--jobs 10"

For each job (best scored first): compose -> build -> verify -> review rounds, exactly as a card would. Prints
the review score, rounds, verifier result and refused edits, and saves each PDF and its LaTeX to
Data/user/trials/ so you can look at them. Costs a few gpt-4o-mini calls per job (about $0.01-0.02).
"""

import argparse
import asyncio
import json
import sys

from sqlalchemy import text

from jobpilot.core.config import REPO_ROOT
from jobpilot.db.queries import profile_items, requirements, resume_docs
from jobpilot.db.queries import scores as scores_repo
from jobpilot.db.session import dispose_engine, read
from jobpilot.llm.client import LLMClient
from jobpilot.models.profile import fact_texts
from jobpilot.models.requirements import Requirements
from jobpilot.models.resume_doc import ResumeDoc
from jobpilot.resume import tailor, tex_versions
from jobpilot.safety import claims

OUT = REPO_ROOT / "Data" / "user" / "trials"


async def main() -> None:
    """Tailor resumes for the best-scored jobs and save the PDFs, LaTeX and results."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=10)
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    OUT.mkdir(parents=True, exist_ok=True)
    async with read() as conn:
        base = await resume_docs.current(conn)
        confirmed = await profile_items.list_all(conn, statuses=("confirmed",))
        facts = fact_texts(confirmed)
        ids = [
            r.job_id
            for r in (
                await conn.execute(
                    text(
                        "select distinct on (s.job_id) s.job_id, s.score from scores s "
                        "order by s.job_id, s.id desc"
                    )
                )
            ).all()
        ]
        rows = []
        for job_id in ids:
            score = await scores_repo.latest_for(conn, job_id)
            job = (
                (await conn.execute(text("select * from jobs where id = :i"), {"i": job_id})).mappings().one()
            )
            req = await requirements.get(conn, score["requirements_id"]) if score else None
            if score and req:
                rows.append((float(score["score"]), job, score, req))
    rows.sort(key=lambda r: -r[0])
    base_doc = ResumeDoc.model_validate(base["doc"])  # type: ignore[index]
    llm, summary = LLMClient(), []
    for _s, job, score, req in rows[: args.jobs]:
        result = await tailor.tailor(
            llm,
            base_doc,
            job={"title": job["title"], "company": job["company"], "jd": job["jd_text"]},
            profile_items=facts,
            rows=confirmed,
            run_id=None,
            gap=list(score["gap"]),
            requirements=Requirements.model_validate(req["data"]),
        )
        stem = OUT / f"job-{job['id']}"
        stem.with_suffix(".pdf").write_bytes(result.pdf)
        stem.with_suffix(".tex").write_text(result.tex)
        rounds = [
            {"round": r["round"], "score": r.get("score"), "verify": (r.get("verify") or {}).get("problems"),
             "applied": [f"{a['section']}: {a['reason']}" for a in r["applied"]], "rejected": r["rejected"],
             "trimmed": r.get("trimmed")}
            for r in result.rounds
        ]  # fmt: skip
        stem.with_suffix(".json").write_text(
            json.dumps({"review": result.review, "rounds": rounds}, indent=1)
        )
        # Independent truth check of the final copy: every skill and title word in it is backed.
        unbacked = claims.unsupported(
            tex_versions.plain(result.tex),
            [
                tex_versions.plain(tailor.tex_versions.base_tex(base_doc)),
                *facts.values(),
                ", ".join(result.added),
            ],
        )
        line = {
            "job": job["id"],
            "company": job["company"],
            "title": job["title"][:50],
            "review": (result.review or {}).get("score"),
            "passed": (result.review or {}).get("passed"),
            "rounds": len(result.rounds),
            "verifier_ok": result.verify.get("ok"),
            "edits": len(result.applied),
            "refused": len(result.rejected),
            "added_skills": result.added,
            "unbacked_claims": unbacked,
            "top_issue": ((result.review or {}).get("issues") or [{}])[0].get("problem"),
        }
        summary.append(line)
        print(json.dumps(line))
    await dispose_engine()
    reached = sum(1 for s in summary if s["passed"])
    verified = sum(1 for s in summary if s["verifier_ok"])
    unbacked = sum(len(s["unbacked_claims"]) for s in summary)
    print(f"\n{reached}/{len(summary)} reached 95+; verifier ok: {verified}; unbacked claims: {unbacked}")
    print(f"PDFs and LaTeX: {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
