"""Copy every resume PDF the database refers to from one Supabase project's storage to another.

Used when moving to a new Supabase project. Reads the keys from the *new* database (after the restore),
fetches each file from the old project and uploads it to the new one. Files already in the new project are
skipped, so it is safe to run again.

    OLD_SUPABASE_URL=... OLD_SUPABASE_SERVICE_ROLE_KEY=... uv run python scripts/copy_storage.py

The new project's SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and DATABASE_URL come from .env as usual.
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import select

from jobpilot.core.config import get_settings
from jobpilot.db.session import dispose_engine, read
from jobpilot.db.tables import resume_doc, resume_versions
from jobpilot.integrations.storage import NotFound, SupabaseStore


async def main() -> None:
    """Copy every referenced resume PDF from the old Supabase project to the new one."""
    old_url, old_key = os.environ.get("OLD_SUPABASE_URL"), os.environ.get("OLD_SUPABASE_SERVICE_ROLE_KEY")
    s = get_settings()
    new_key = s.supabase_service_role_key.get_secret_value() if s.supabase_service_role_key else ""
    if not (old_url and old_key and s.supabase_url and new_key):
        sys.exit("Set OLD_SUPABASE_URL and OLD_SUPABASE_SERVICE_ROLE_KEY, and the new project's keys in .env")
    if old_url.rstrip("/") == s.supabase_url.rstrip("/"):
        sys.exit("The old and new projects are the same - check SUPABASE_URL in .env")
    bucket = s.supabase_storage_bucket
    old, new = SupabaseStore(old_url, old_key, bucket), SupabaseStore(s.supabase_url, new_key, bucket)
    await new.ensure_bucket()

    async with read() as conn:
        keys = {k for (k,) in (await conn.execute(select(resume_versions.c.pdf_key))).all() if k}
        keys |= {k for (k,) in (await conn.execute(select(resume_doc.c.pdf_key))).all() if k}
    copied = skipped = missing = 0
    for key in sorted(keys):
        # Already in the new project: skip, so a re-run is cheap.
        try:
            await new.get(key)
            skipped += 1
            continue
        except NotFound:
            pass
        try:
            data = await old.get(key)
        except NotFound:
            missing += 1
            print(f"not in the old project: {key}")
            continue
        await new.put(key, data)
        copied += 1
    print(f"{len(keys)} files referenced: {copied} copied, {skipped} already there, {missing} missing")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
