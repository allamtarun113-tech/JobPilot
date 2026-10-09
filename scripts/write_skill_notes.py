"""Write a skill note (plus 5-8 interview Q&A for the Q&A bank) for every skill you have, once.

    make skill-notes                      # your confirmed profile skills that have no note yet
    make skill-notes ARGS="--gaps 20"     # also the 20 most common missing skills in your scored jobs
    make skill-notes ARGS="--skills kafka,docker --force"   # these skills, rewriting existing notes

One gpt-4o-mini call per skill (about $0.002 each). Notes and Q&A are stored in Qdrant (decision 28).
"""

import argparse
import asyncio
import sys
from collections import Counter

from sqlalchemy import select

from jobpilot.db.queries import profile_items
from jobpilot.db.session import dispose_engine, read
from jobpilot.db.tables import scores
from jobpilot.integrations.vectors import VectorStore
from jobpilot.llm.client import LLMClient
from jobpilot.llm.embeddings import default_embedder
from jobpilot.prep import skill_notes
from jobpilot.reference import catalog


async def wanted(gaps: int) -> list[str]:
    """Return the skills to write notes for: yours, then the most common gaps.

    Args:
        gaps: How many of the most common missing skills to add.
    """
    async with read() as conn:
        items = await profile_items.list_all(conn, statuses=("confirmed",))
        gap_rows = (await conn.execute(select(scores.c.gap))).all() if gaps else []
    mine = [sid for i in items if i["kind"] == "skill" and (sid := catalog.skill_id(i["text"]))]
    missing = Counter(g["skill_id"] for (row,) in gap_rows for g in row or [] if g.get("skill_id"))
    return list(dict.fromkeys([*mine, *(sid for sid, _n in missing.most_common(gaps))]))


async def main() -> None:
    """Write a skill note for each wanted skill, skipping existing ones unless --force."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--skills", default="", help="comma-separated skill ids or names")
    parser.add_argument("--gaps", type=int, default=0, help="also the N most common missing skills")
    parser.add_argument("--force", action="store_true", help="rewrite notes that already exist")
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]  # progress shows in log files too
    named = [catalog.skill_id(s) or s.strip() for s in args.skills.split(",") if s.strip()]
    skill_ids = named or await wanted(args.gaps)
    vectors, embedder, llm = VectorStore.from_settings(), default_embedder(), LLMClient()
    written = skipped = failed = qa = 0
    try:
        for sid in skill_ids:
            if catalog.get(sid) is None:
                print(f"  ? {sid}: not in the skills taxonomy")
                failed += 1
                continue
            if not args.force and await skill_notes.exists(vectors, sid):
                skipped += 1
                continue
            try:
                note, saved = await skill_notes.write(llm, vectors, embedder, sid)
            except Exception as exc:
                print(f"  ! {sid}: {type(exc).__name__}: {exc}")
                failed += 1
                continue
            written, qa = written + 1, qa + saved
            print(
                f"  + {sid}: {len(note.key_concepts)} concepts, {saved} Q&A, {len(note.citations)} citations"
            )
    finally:
        await vectors.close()
        await dispose_engine()
    print(f"{written} notes written, {skipped} already there, {failed} failed; {qa} Q&A added to the bank")


if __name__ == "__main__":
    asyncio.run(main())
