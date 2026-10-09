"""Every public table gets row-level security (Supabase's REST API must expose nothing)."""

from sqlalchemy import text

from jobpilot.db.session import transaction
from scripts.db_lockdown import lockdown


async def test_every_public_table_has_rls() -> None:
    """After lockdown no public table is left without row-level security."""
    tables = await lockdown()
    assert {"runs", "outbox", "profile_items", "checkpoints"} <= set(tables)
    async with transaction() as conn:
        open_tables = (
            (
                await conn.execute(
                    text(
                        "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert open_tables == []
