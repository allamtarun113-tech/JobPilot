"""Heading-aware PDF ingestion. Sections, never token windows, are the chunk boundary."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from pypdf import PdfReader

from jobpilot.core.config import REPO_ROOT
from jobpilot.integrations.vectors import PREP_CHUNKS, VectorStore
from jobpilot.llm.embeddings import Embedder

MANIFEST = REPO_ROOT / "Data" / "books" / "books.json"
EMBED_BATCH_SIZE = 256
EMBED_TEXT_LIMIT = 12_000
# Heading heuristic for books without bookmarks: "Chapter 3",
# numbered headings ("2.1 Indexes") or ALL-CAPS lines.
HEADING = re.compile(
    r"^(?:(?i:chapter)\s+\d+|\d+(?:\.\d+)*(?:[.)])?\s+\S.{0,100}|[A-Z][A-Z0-9 &:/,+#-]{4,100})$",
)


@dataclass(frozen=True)
class Chunk:
    """One book section: its topic, pages, skills and text."""

    id: int
    book_id: str
    source: str
    topic: str
    page_start: int
    page_end: int
    skill_ids: list[str]
    text: str

    def payload(self) -> dict[str, Any]:
        """Return the Qdrant payload stored with the chunk."""
        return {
            "book_id": self.book_id,
            "source": self.source,
            "topic": self.topic,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "skill_ids": self.skill_ids,
            "text": self.text,
        }


def manifest() -> list[dict[str, Any]]:
    """Return the books marked for prep in ``Data/books/books.json``."""
    return [book for book in json.loads(MANIFEST.read_text())["books"] if book["use"] == "prep"]


def _outline(reader: PdfReader) -> dict[int, list[str]]:
    """Map 0-based page numbers to the bookmark titles that start on them."""
    headings: dict[int, list[str]] = {}

    def walk(items: list[Any]) -> None:
        """Collect bookmarks from a (nested) outline list."""
        for item in items:
            if isinstance(item, list):
                walk(item)
                continue
            try:
                page = reader.get_destination_page_number(item)
            except Exception:
                continue
            if page is None:
                continue
            title = str(getattr(item, "title", "")).strip()
            if title:
                headings.setdefault(page, []).append(title)

    walk(list(reader.outline or []))
    return headings


def _heading_positions(lines: list[str], candidates: list[str] | None) -> list[tuple[int, str]]:
    """Find which lines on a page are headings.

    Args:
        lines: The page's non-empty lines.
        candidates: Bookmark titles expected on this page, or None to
            use the heading heuristic.

    Returns:
        (line index, heading title) pairs.
    """
    positions: list[tuple[int, str]] = []
    if candidates is not None:
        remaining = list(candidates)
        for index, line in enumerate(lines):
            normalized = " ".join(line.lower().split())
            for title in list(remaining):
                needle = " ".join(title.lower().split())
                if needle and (needle in normalized or normalized in needle):
                    positions.append((index, title))
                    remaining.remove(title)
                    break
    else:
        for index, line in enumerate(lines):
            clean = " ".join(line.split()).strip()
            if 3 <= len(clean) <= 110 and HEADING.fullmatch(clean):
                positions.append((index, clean))
    return positions


def split_pages(
    pages: list[str],
    *,
    book_id: str,
    source: str,
    skill_ids: list[str],
    headings: dict[int, list[str]] | None,
) -> list[Chunk]:
    """Split a book's pages into one chunk per section.

    A section runs from one heading to the next, across pages if
    needed; it never cuts through a heading.

    Args:
        pages: The text of each page.
        book_id: The manifest id.
        source: The book title shown in citations.
        skill_ids: Skills the whole book is tagged with.
        headings: Page index -> bookmark titles, or None for the
            heuristic.

    Returns:
        The chunks (sections under 40 characters are dropped).
    """
    sections: list[tuple[str, int, int, list[str]]] = []
    topic, start, collected = book_id.replace("-", " ").title(), 1, []
    for page_index, page_text in enumerate(pages):
        page = page_index + 1
        lines = [line.strip() for line in page_text.splitlines() if line.strip()]
        found = _heading_positions(lines, headings.get(page_index, []) if headings is not None else None)
        cursor = 0
        for line_index, title in found:
            collected.extend(lines[cursor:line_index])
            if " ".join(collected).strip():
                sections.append((topic, start, page, collected))
            topic, start, collected, cursor = title, page, [], line_index + 1
        collected.extend(lines[cursor:])
    if collected:
        sections.append((topic, start, len(pages), collected))
    chunks = []
    for topic, page_start, page_end, lines in sections:
        body = "\n".join(lines).strip()
        if len(body) < 40:
            continue
        # Stable id from book, page and topic: re-ingesting replaces
        # points.
        raw_id = f"{book_id}:{page_start}:{topic}".encode()
        point_id = int.from_bytes(hashlib.sha256(raw_id).digest()[:8], "big") & ((1 << 63) - 1)
        chunks.append(Chunk(point_id, book_id, source, topic, page_start, page_end, skill_ids, body))
    return chunks


def read_book(book: dict[str, Any]) -> list[Chunk]:
    """Extract a book's text with pdftotext and split it into chunks.

    Raises:
        RuntimeError: pdftotext (Poppler) isn't installed.
    """
    path = REPO_ROOT / "Data" / "books" / book["file"]
    reader = PdfReader(path)
    executable = shutil.which("pdftotext")
    if executable is None:
        raise RuntimeError("prep ingestion requires pdftotext (install Poppler)")
    extracted = subprocess.run(
        [executable, "-layout", str(path), "-"],
        check=True,
        capture_output=True,
        timeout=120,
    ).stdout.decode("utf-8", errors="replace")
    # pdftotext separates pages with form feeds.
    pages = extracted.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    headings = _outline(reader) if book["has_bookmarks"] else None
    return split_pages(
        pages,
        book_id=book["id"],
        source=book["title"],
        skill_ids=list(book["skill_ids"]),
        headings=headings,
    )


async def ingest_all(vectors: VectorStore, embedder: Embedder) -> dict[str, int]:
    """Rebuild the prep_chunks collection from every prep book.

    Returns:
        How many books and chunks were ingested.
    """
    chunks = [chunk for book in manifest() for chunk in read_book(book)]
    points: list[tuple[int, list[float], dict[str, Any]]] = []
    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[start : start + EMBED_BATCH_SIZE]
        embeddings = await embedder.embed(
            [f"{chunk.topic}\n{chunk.text}"[:EMBED_TEXT_LIMIT] for chunk in batch]
        )
        points.extend(
            (chunk.id, vector, chunk.payload()) for chunk, vector in zip(batch, embeddings, strict=True)
        )
    await vectors.replace_all(PREP_CHUNKS, points)
    return {"books": len(manifest()), "chunks": len(chunks)}
