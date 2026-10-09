"""Enable row-level security on every table in the public schema (no policies = deny all).

Supabase exposes the public schema through its auto-generated REST API (anon / authenticated roles).
JobPilot never uses that API: the app connects as the table owner, which bypasses RLS. Locking every table
means a leaked anon key exposes nothing. Idempotent; run by `make migrate` after Alembic and the checkpointer.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from jobpilot.db.session import dispose_engine, get_engine


async def lockdown() -> list[str]:
    """Enable row-level security on every public table.

    Returns:
        The table names.
    """
    async with get_engine().begin() as conn:
        rows = await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
        tables = [r[0] for r in rows]
        for table in tables:
            await conn.execute(text(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY'))
    return tables


async def main() -> None:
    """Lock every table down and report how many."""
    tables = await lockdown()
    print(f"row-level security enabled on {len(tables)} tables")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
