"""Seed draft profile items from a YAML file (for facts that are not on the resume).

File format (Data/user/profile_seed.yaml):
  items:
    - {kind: skill, text: Docker, evidence: "used for the analytics pipeline at Turing"}
    - {kind: project, text: "Built ...", evidence: "personal project, 2025"}
Items are created as drafts - confirm them on the dashboard (/profile). Never confirmed on your behalf.

Usage: uv run python scripts/seed_profile.py [Data/user/profile_seed.yaml]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import yaml

from jobpilot.core.config import REPO_ROOT
from jobpilot.db.queries import profile_items
from jobpilot.db.session import dispose_engine, transaction
from jobpilot.models.profile import ItemKind, ItemSource


async def main(path: Path) -> None:
    """Create draft profile items from a YAML file (never confirmed here)."""
    text = await asyncio.to_thread(path.read_text)
    items = (yaml.safe_load(text) or {}).get("items") or []
    async with transaction() as conn:
        for item in items:
            kind = ItemKind(item["kind"])
            await profile_items.create(
                conn,
                kind=kind.value,
                text=str(item["text"]),
                evidence=str(item.get("evidence") or path.name),
                source=ItemSource.NOTES.value,
            )
    print(f"{len(items)} draft item(s) added - confirm them at /profile")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "Data/user/profile_seed.yaml"))
