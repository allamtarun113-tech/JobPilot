"""The verifier (decision 31): build the copy, read it back, check it.

Builds this job's resume copy in the sandbox, reads the PDF back as an
applicant tracking system would, and checks it. Every check is
mechanical, so it is code, not a model:

- the LaTeX compiles (no network, no shell);
- the page limit holds;
- the name and every section heading are there, in order;
- no broken characters (replacement glyphs, leaked LaTeX commands);
- every applied edit's new words actually appear in the PDF.

Problems go back to the editor and the reviewer for the next round.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

from jobpilot.core import cpu
from jobpilot.resume import parse_check, render, tex_versions

# Leaked command names, the replacement glyph, or "??" (missing
# references) mean broken output.
_LEAKED = re.compile(r"\\[a-zA-Z]{2,}|\ufffd|\?\?")


def _flat(text: str) -> str:
    """Letters and digits only, lower-cased: line breaks and hyphenation in the PDF don't matter."""
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[^a-z0-9]+", "", text)


@dataclass
class VerifyReport:
    """Everything the verifier found about one built copy."""

    ok: bool
    pages: int = 0
    max_pages: int = 1
    missing: list[str] = field(default_factory=list)
    out_of_order: list[str] = field(default_factory=list)
    broken: list[str] = field(default_factory=list)
    not_landed: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # worth knowing, not a failure (e.g. over the page target)

    def as_dict(self) -> dict[str, Any]:
        """Return the report as a plain dict (stored as JSON)."""
        return asdict(self)


def check_text(
    text: str, pages: int, tex: str, applied: list[dict[str, Any]], *, name: str, max_pages: int
) -> VerifyReport:
    """Check the text read back from a built PDF.

    Args:
        text: The PDF's text.
        pages: Its page count.
        tex: The LaTeX it was built from (for the section titles).
        applied: The edits applied to the copy.
        name: The candidate's name, expected first.
        max_pages: The page limit.

    Returns:
        The report; ok when no problem was found.
    """
    report = VerifyReport(ok=False, pages=pages, max_pages=max_pages)
    haystack, cursor = _flat(text), 0
    for label in [name, *tex_versions.section_titles(tex)]:
        needle = _flat(label)
        pos = haystack.find(needle, cursor)
        if pos >= 0:
            cursor = pos + len(needle)
        elif needle in haystack:
            report.out_of_order.append(label)
        else:
            report.missing.append(label)
    report.broken = sorted(set(_LEAKED.findall(text)))[:5]
    for edit in applied:
        words = _flat(str(edit.get("after") or ""))
        # Compare the start and end of the new text: wrapping and
        # hyphenation can split the middle.
        if words and not (words[:40] in haystack and words[-40:] in haystack):
            report.not_landed.append(f"{edit.get('section')}: {str(edit.get('after'))[:60]}")
    if pages > max_pages:
        report.problems.append(f"the PDF is {pages} pages; it must fit on {max_pages} - shorten or cut lines")
    if report.missing:
        report.problems.append("missing from the PDF: " + ", ".join(report.missing))
    if report.out_of_order:
        report.problems.append("out of order in the PDF: " + ", ".join(report.out_of_order))
    if report.broken:
        report.problems.append("broken characters in the PDF: " + " ".join(report.broken))
    if report.not_landed:
        report.problems.append("edits not visible in the PDF: " + "; ".join(report.not_landed))
    report.ok = not report.problems
    return report


async def verify(
    tex: str, applied: list[dict[str, Any]], *, name: str, max_pages: int
) -> tuple[bytes | None, VerifyReport]:
    """(the PDF, or None if it didn't compile; the report)."""
    try:
        pdf = await render.compile_pdf(tex)
    except render.CompileError as exc:
        tail = " ".join(str(exc).split())[-300:]
        return None, VerifyReport(
            ok=False, max_pages=max_pages, problems=[f"the LaTeX did not compile: {tail}"]
        )
    text, pages = await cpu.run(parse_check.extract_text, pdf)
    return pdf, check_text(text, pages, tex, applied, name=name, max_pages=max_pages)


async def read_back(pdf: bytes) -> str:
    """Return the text of a PDF, extracted in the CPU pool."""
    text, _pages = await cpu.run(parse_check.extract_text, pdf)
    return text
