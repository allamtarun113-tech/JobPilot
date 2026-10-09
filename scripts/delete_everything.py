"""Delete everything JobPilot stores about you.

Removes: every application table row and LangGraph checkpoint in DATABASE_URL, private files in the
object store (resume PDFs), the Qdrant profile/job vectors, the encrypted Gmail token, dry-run logs and old
alert-email samples. Keeps: reference data (Data/books, Data/learning, Data/taxonomy) and the Prepare
corpus vectors, which hold nothing personal. It does NOT reach third parties: revoke Gmail access in your
Google account, delete the Telegram chat, delete Langfuse traces, rotate keys and delete backups you keep
elsewhere (see docs/runbook.md).

Default is a dry run that lists what would go. To really delete, stop all processes, then:
  uv run python scripts/delete_everything.py --yes-delete-everything --database-host <make db-target host>
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
from urllib.parse import urlsplit

from sqlalchemy import text

from jobpilot.core.config import REPO_ROOT, get_config, get_settings
from jobpilot.db.session import dispose_engine, get_engine
from jobpilot.db.tables import metadata
from jobpilot.integrations.vectors import JOBS, PROFILE_ITEMS, VectorStore

LOCAL_PATHS = ["var/gmail_token.enc", "var/telegram_dry_run.jsonl", "var/alert_failures"]
CHECKPOINTS = ["checkpoints", "checkpoint_blobs", "checkpoint_writes"]


async def main(args: argparse.Namespace) -> None:
    """List (dry run) or delete everything JobPilot stores about the user.

    Deletes only with --yes-delete-everything and a --database-host that
    matches the configured database, so the wrong database can't be
    wiped by accident.
    """
    host = urlsplit(get_settings().psycopg_dsn).hostname or "local socket"
    tables = [t.name for t in metadata.sorted_tables] + CHECKPOINTS
    storage = REPO_ROOT / get_config().resume.storage_dir
    print(f"database {host}: TRUNCATE {len(tables)} tables")
    bucket = get_settings().supabase_storage_bucket
    print(f"object store: {'Supabase bucket ' + bucket if get_settings().supabase_url else storage}")
    print(f"vectors: {PROFILE_ITEMS}, {JOBS}; local files: {', '.join(LOCAL_PATHS)}")
    # Both flags must match before anything is deleted.
    if not args.yes_delete_everything or args.database_host != host:
        print("Dry run. Nothing was deleted.")
        return
    async with get_engine().begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    await dispose_engine()
    vectors = VectorStore.from_settings()
    for collection in (PROFILE_ITEMS, JOBS):
        await vectors.replace_all(collection, [])
    await vectors.close()
    if storage.exists():
        shutil.rmtree(storage)
    for rel in LOCAL_PATHS:
        path = REPO_ROOT / rel
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    if get_settings().supabase_url:
        print("Supabase Storage: delete the 'resumes/' folder in the bucket from the Supabase dashboard.")
    print("Deleted. Now revoke Gmail access, delete the Telegram chat and Langfuse traces, and old backups.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes-delete-everything", action="store_true")
    parser.add_argument("--database-host", default="")
    asyncio.run(main(parser.parse_args()))
