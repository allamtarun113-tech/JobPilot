"""Page 1 of a resume PDF as a PNG image, so the card shows the resume without opening the PDF (decision 32).

Rendered with pypdfium2 (no system tools needed, works in the cloud container too), in the CPU pool.
"""

from __future__ import annotations

import io

from jobpilot.core import cpu

SCALE = 2.0  # 144 dpi: sharp on a phone, ~150-300 KB


def first_page_png(pdf: bytes, scale: float = SCALE) -> bytes:
    """Render page 1 of a PDF as PNG bytes.

    Args:
        pdf: The PDF.
        scale: Render scale (1.0 = 72 dpi).

    Returns:
        The PNG image.
    """
    import pypdfium2 as pdfium  # backend-only package: the dashboard never imports this module

    document = pdfium.PdfDocument(pdf)
    try:
        image = document[0].render(scale=scale).to_pil()
        out = io.BytesIO()
        image.convert("RGB").save(out, format="PNG", optimize=True)
        return out.getvalue()
    finally:
        document.close()


async def render(pdf: bytes) -> bytes:
    """Render page 1 as PNG in the CPU pool."""
    return await cpu.run(first_page_png, pdf)


def key_for(pdf_key: str) -> str:
    """The preview sits next to its PDF: resumes/jobs/7/abc.pdf -> resumes/jobs/7/abc.png."""
    return pdf_key.removesuffix(".pdf") + ".png"
