"""The base resume as structured data: import from the user's .tex, load, render and store."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.engine import RowMapping

from jobpilot.core.errors import JobPilotError
from jobpilot.db.queries import profile_items, resume_docs
from jobpilot.db.session import transaction
from jobpilot.integrations.storage import get_store
from jobpilot.models.profile import ItemSource
from jobpilot.models.resume_doc import ResumeDoc
from jobpilot.resume import parse_check, render
from jobpilot.resume.profile_drafts import draft_from_resume
from jobpilot.resume.tex_import import import_tex


class ResumeExists(JobPilotError):
    """A base resume already exists and replacing it wasn't asked for."""

    pass


@dataclass(frozen=True)
class ImportResult:
    """What an import created: the resume id, item count and element count."""

    resume_id: int
    items_created: int
    elements: int


def link_evidence(doc: ResumeDoc, links: dict[str, list[str]], ids: dict[str, int]) -> None:
    """Point each resume element at the profile item ids it rests on.

    Args:
        doc: The resume, changed in place.
        links: Element id -> import keys of its items.
        ids: Import key -> the stored item id.
    """
    for section in doc.sections:
        for entry in section.entries:
            entry.evidence = [ids[k] for k in links.get(entry.id, []) if k in ids]
            for bullet in entry.bullets:
                bullet.evidence = [ids[k] for k in links.get(bullet.id, []) if k in ids]


async def import_from_tex(path: Path, *, replace: bool = False) -> ImportResult:
    """Import the user's .tex as the base resume, in one transaction.

    Parses the .tex, creates draft profile items and stores the
    structured resume with its evidence links.

    Args:
        path: The .tex file.
        replace: Allow replacing an existing base resume.

    Returns:
        What was created.

    Raises:
        ResumeExists: A base resume exists and ``replace`` is False.
    """
    doc = import_tex(await asyncio.to_thread(path.read_text))
    draft = draft_from_resume(doc)
    async with transaction() as conn:
        if await resume_docs.current(conn) is not None and not replace:
            raise ResumeExists("a base resume already exists (use --replace)")
        ids = await profile_items.store_drafts(conn, draft.items, ItemSource.RESUME)
        link_evidence(doc, draft.links, ids)
        resume_id = await resume_docs.replace_current(
            conn, doc=doc.model_dump(mode="json"), template_id=doc.template_id, source_file=path.name
        )
    return ImportResult(resume_id, len(ids), sum(1 for _ in doc.elements()))


async def load_current() -> tuple[RowMapping, ResumeDoc] | None:
    """Return the current base resume row and its document, or None."""
    async with transaction() as conn:
        row = await resume_docs.current(conn)
    if row is None:
        return None
    return row, ResumeDoc.model_validate(row["doc"])


async def render_current() -> dict[str, Any]:
    """Render the current base resume, run the parse check and store the PDF.

    Compiling happens outside any transaction; the result is written
    with a version check.

    Returns:
        The stored PDF key and the parse report.
    """
    loaded = await load_current()
    if loaded is None:
        raise JobPilotError("no base resume imported yet")
    row, doc = loaded
    pdf = await render.render_pdf(doc)
    report = (await parse_check.acheck(doc, pdf)).as_dict()
    key = f"resumes/base/{row['id']}-v{row['version']}.pdf"
    await get_store().put(key, pdf)
    async with transaction() as conn:
        await resume_docs.save_render(
            conn, row["id"], row["version"], pdf_key=key, parse_ok=report["ok"], report=report
        )
    return {"pdf_key": key, **report}
