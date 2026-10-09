"""Import the base resume from the user's .tex into structured data + draft profile items, then render it.

Usage: uv run python scripts/import_resume.py [Data/user/resume.tex] [--replace]
Then confirm the items on the dashboard (/profile).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from jobpilot.core.config import REPO_ROOT
from jobpilot.db.session import dispose_engine
from jobpilot.resume import doc


async def main(args: list[str]) -> None:
    """Import the .tex resume, create draft profile items and render the PDF."""
    replace = "--replace" in args
    paths = [a for a in args if not a.startswith("--")]
    path = Path(paths[0]) if paths else REPO_ROOT / "Data" / "user" / "resume.tex"
    try:
        result = await doc.import_from_tex(path, replace=replace)
    except doc.ResumeExists as exc:
        sys.exit(str(exc))
    print(
        f"imported resume #{result.resume_id}: {result.items_created} draft profile items, "
        f"{result.elements} resume elements"
    )
    report = await doc.render_current()
    print(
        f"rendered {report['pdf_key']}: {report['pages']} page(s), parse check "
        f"{'OK' if report['ok'] else 'FAILED ' + str(report)}"
    )
    print("Next: confirm your profile items on the dashboard -> /profile")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
