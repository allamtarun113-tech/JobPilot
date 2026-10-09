"""Parse check: what an applicant tracking system will see.

A plain-text extractor must recover every visible section (and entry
title) in order, within the page limit.
"""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

from pypdf import PdfReader

from jobpilot.core import cpu
from jobpilot.models.resume_doc import ResumeDoc


@dataclass
class ParseReport:
    """The parse check result: pages, and labels missing or out of order."""

    ok: bool
    pages: int
    max_pages: int
    missing: list[str] = field(default_factory=list)
    out_of_order: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return the report as a plain dict (stored as JSON)."""
        return asdict(self)


def _squash(text: str) -> str:
    """Normalize text for matching: NFKC, lower case, plain dashes, no spaces."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("–", "-").replace("—", "-")
    # Extractors split words unpredictably, so compare without any
    # whitespace.
    return re.sub(r"[\s​]+", "", text)


def extract_text(pdf: bytes) -> tuple[str, int]:
    """Return the PDF's text and page count."""
    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join(page.extract_text() or "" for page in reader.pages), len(reader.pages)


def check(doc: ResumeDoc, pdf: bytes) -> ParseReport:
    """Run the parse check on a rendered PDF (in this thread)."""
    return _report(doc, *extract_text(pdf))


async def acheck(doc: ResumeDoc, pdf: bytes) -> ParseReport:
    """The same check with PDF text extraction in the CPU pool (off the event loop)."""
    text, pages = await cpu.run(extract_text, pdf)
    return _report(doc, text, pages)


def _report(doc: ResumeDoc, text: str, pages: int) -> ParseReport:
    """Check that every expected label appears in the text, in order.

    Args:
        doc: The resume that was rendered.
        text: The text extracted from the PDF.
        pages: The PDF's page count.

    Returns:
        The report; ok only with nothing missing or out of order and
        within the page limit.
    """
    haystack = _squash(text)
    expected: list[str] = [doc.header.name]
    for section in doc.visible_sections():
        expected.append(section.title)
        expected += [e.title for e in section.entries if e.title and not e.hidden]
    missing, out_of_order, cursor = [], [], 0
    for label in expected:
        needle = _squash(label)
        pos = haystack.find(needle, cursor)
        if pos >= 0:
            cursor = pos + len(needle)
        # Present but earlier than expected: the order is wrong.
        elif haystack.find(needle) >= 0:
            out_of_order.append(label)
        else:
            missing.append(label)
    ok = not missing and not out_of_order and pages <= doc.max_pages
    return ParseReport(
        ok=ok, pages=pages, max_pages=doc.max_pages, missing=missing, out_of_order=out_of_order
    )
