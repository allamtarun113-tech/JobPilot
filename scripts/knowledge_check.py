"""Show what the knowledge base holds, and time one skill lookup (notes + resources + Q&A, no model call).

make knowledge-check SKILL=python
"""

import asyncio
import sys
import time

from jobpilot.integrations.vectors import LEARNING_RESOURCES, PREP_CHUNKS, QA_BANK, SKILL_NOTES, VectorStore
from jobpilot.knowledge import lookup
from jobpilot.reference import catalog


async def main() -> None:
    """Print collection sizes and time one skill lookup, cold and warm."""
    name = sys.argv[1] if len(sys.argv) > 1 else "python"
    skill_id = catalog.skill_id(name) or name
    vectors = VectorStore.from_settings()
    try:
        for collection in (PREP_CHUNKS, LEARNING_RESOURCES, SKILL_NOTES, QA_BANK):
            print(f"{collection:20} {await vectors.count(collection):>6}")
        start = time.perf_counter()
        found = await lookup.for_skill(vectors, skill_id)
        cold = time.perf_counter() - start
        start = time.perf_counter()
        found = await lookup.for_skill(vectors, skill_id)  # warm, as in the running worker
        warm = time.perf_counter() - start
    finally:
        await vectors.close()
    note = "yes" if found.note else "no"
    print(f"\n{skill_id}: note={note}, {len(found.resources)} resources, {len(found.qa)} Q&A")
    print(
        f"lookup took {warm * 1000:.0f} ms warm, {cold * 1000:.0f} ms on a fresh connection (no model call)"
    )
    if found.note:
        print(f"  summary: {found.note['summary'][:200]}")
    for r in found.resources[:3]:
        print(f"  resource: {r['title']} ({r['url']})")
    for q in found.qa[:2]:
        print(f"  Q: {q['question']}")


if __name__ == "__main__":
    asyncio.run(main())
