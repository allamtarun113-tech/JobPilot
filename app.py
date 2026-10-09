"""Vercel entrypoint for the repository's src-layout application."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from jobpilot.web.main import app  # noqa: F401
