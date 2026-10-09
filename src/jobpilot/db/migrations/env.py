"""Alembic environment. The database URL comes from core/config.py (DATABASE_URL)."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from jobpilot.core.config import get_settings
from jobpilot.db.tables import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata

# Tables owned by other tools (LangGraph's checkpointer) are never touched by our migrations.
EXTERNAL_TABLES = {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}


def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    """Tell Alembic which database objects to compare.

    Tables owned by LangGraph exist in the database but not in our
    metadata; without this filter, autogenerate would drop them.

    Returns:
        False for reflected-only objects of an external table.
    """
    # Indexes are judged by the table they belong to.
    table = obj.table.name if type_ == "index" else name
    return not (reflected and compare_to is None and table in EXTERNAL_TABLES)


def run_migrations_offline() -> None:
    """Write the migration SQL instead of running it (``--sql``)."""
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run the migrations against the database in one transaction."""
    engine = create_engine(get_settings().database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, include_object=include_object
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
