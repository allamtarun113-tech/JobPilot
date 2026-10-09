"""The hosted entrypoint must work without an editable project installation."""

import os
import subprocess
import sys
from pathlib import Path


def test_entrypoint_supplies_src_import_path() -> None:
    """app.py finds the src package without an editable install."""
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.update(JOBPILOT_ENV="test", VERCEL="1", DASHBOARD_SESSION_SECRET="test-session-key")
    code = """
import sys, runpy
from pathlib import Path
root = Path.cwd()
sys.path = [p for p in sys.path if Path(p or '.').resolve() != root / 'src']
namespace = runpy.run_path('app.py')
from jobpilot.core.config import REPO_ROOT
assert REPO_ROOT == root
assert namespace['app'].title == 'JobPilot'
assert str(root / 'src') in sys.path
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_dashboard_never_loads_backend_only_packages() -> None:
    """Importing the dashboard loads no backend-only package.

    Vercel installs only the dashboard's dependencies (decision 26), so
    the vector client, the embedding model, the AI SDK, LangGraph and
    the PDF reader must stay unloaded.
    """
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.update(JOBPILOT_ENV="test")
    code = """
import sys
import jobpilot.web.main
heavy = [m for m in ('qdrant_client', 'fastembed', 'openai', 'langgraph', 'pypdf') if m in sys.modules]
assert not heavy, heavy
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
