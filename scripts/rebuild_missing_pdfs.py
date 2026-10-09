"""Rebuild every resume PDF the database refers to but storage doesn't have (e.g. after moving databases).

    make rebuild-pdfs

Each version is rebuilt from what it was made from - its LaTeX copy (resume v2) or its structured resume - in
the same sandbox, under the same storage key, with its page-1 preview. No model calls.
"""

import asyncio

from sqlalchemy import select

from jobpilot.db.session import dispose_engine, read
from jobpilot.db.tables import resume_doc, resume_versions
from jobpilot.integrations.storage import NotFound, get_store
from jobpilot.models.resume_doc import ResumeDoc
from jobpilot.resume import preview, render


async def exists(key: str) -> bool:
    """Return True if storage already holds an object under ``key``."""
    try:
        await get_store().get(key)
        return True
    except NotFound:
        return False


async def main() -> None:
    """Rebuild each referenced PDF (and its preview) that storage is missing."""
    async with read() as conn:
        versions = (await conn.execute(select(resume_versions))).mappings().all()
        bases = (await conn.execute(select(resume_doc))).mappings().all()
    store, rebuilt, failed = get_store(), 0, 0
    for row in [*versions, *bases]:
        key = row["pdf_key"]
        if not key or await exists(key):
            continue
        try:
            tex = row.get("tex")
            pdf = await (
                render.compile_pdf(tex) if tex else render.render_pdf(ResumeDoc.model_validate(row["doc"]))
            )
            await store.put(key, pdf)
            await store.put(preview.key_for(key), await preview.render(pdf))
            rebuilt += 1
            print(f"  rebuilt {key}")
        except Exception as exc:
            failed += 1
            print(f"  ! {key}: {type(exc).__name__}: {exc}")
    await dispose_engine()
    print(f"{rebuilt} PDFs rebuilt, {failed} failed")


if __name__ == "__main__":
    asyncio.run(main())
