"""Compare row counts of every table in two databases (used after moving to a new Supabase project).

    OLD_DATABASE_URL=postgresql+psycopg://... uv run python scripts/compare_databases.py

DATABASE_URL (the new database) comes from .env as usual. Exits with an error if any table differs.
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from jobpilot.core.config import get_settings

TABLES = text("select tablename from pg_tables where schemaname = 'public' order by tablename")


async def counts(url: str) -> dict[str, int]:
    """Return the row count of every public table in a database."""
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            names = [n for (n,) in (await conn.execute(TABLES)).all()]
            return {n: int(await conn.scalar(text(f'select count(*) from public."{n}"')) or 0) for n in names}
    finally:
        await engine.dispose()


async def main() -> None:
    """Print old vs new row counts per table; exit with an error if any differ."""
    old_url = os.environ.get("OLD_DATABASE_URL")
    if not old_url:
        sys.exit("Set OLD_DATABASE_URL to the old database's connection string")
    old, new = await counts(old_url), await counts(get_settings().database_url)
    bad = 0
    for name in sorted(old.keys() | new.keys()):
        a, b = old.get(name), new.get(name)
        mark = "ok" if a == b else "DIFFERENT"
        bad += a != b
        print(f"{name:32} old={a!s:>6}  new={b!s:>6}  {mark}")
    if bad:
        sys.exit(f"{bad} table(s) differ")
    print("All tables match.")


if __name__ == "__main__":
    asyncio.run(main())
