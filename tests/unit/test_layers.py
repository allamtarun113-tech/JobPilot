"""Import rules for src/jobpilot, checked on every import.

- models import nothing from the app; core imports only core and models.
- Shared code (db, queue, safety, llm, integrations, monitoring, reference, knowledge) never imports
  a feature.
- A feature (sources, matching, resume, hr_email, profile, prep) never imports another feature.
  The coordinating packages (run, bot, cards, chat) may combine features.
- Nothing imports the process entry points (web, worker, dispatcher, servers).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "jobpilot"
SHARED = {"db", "queue", "safety", "llm", "integrations", "monitoring", "reference", "knowledge"}
FEATURES = {"sources", "matching", "resume", "hr_email", "profile", "prep"}
COORDINATORS = {"run", "bot", "cards", "chat"}
PROCESSES = {"web", "worker", "dispatcher", "servers"}


def imports(path: Path) -> set[str]:
    """Return the jobpilot packages a file imports."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        names = [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        for name in names:
            parts = name.split(".")
            if parts[0] == "jobpilot" and len(parts) > 1:
                found.add(parts[1])
    return found


def allowed(package: str) -> set[str]:
    """Return the packages ``package`` may import under the layer rules."""
    if package == "models":
        return {"models"}
    if package == "core":
        return {"core", "models"}
    base = {package, "core", "models"} | SHARED
    if package in SHARED or package in FEATURES:
        return base
    if package in COORDINATORS:
        return base | FEATURES | COORDINATORS
    return base | FEATURES | COORDINATORS | PROCESSES  # a process may use anything


def test_every_import_respects_the_layers() -> None:
    """No file imports a package its layer forbids."""
    violations = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC)
        if len(rel.parts) < 2:
            continue
        package = rel.parts[0]
        for target in imports(path) - allowed(package):
            violations.append(f"{rel} -> {target}")
    assert violations == []
