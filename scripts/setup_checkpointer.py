"""Create LangGraph's checkpoint tables (idempotent). Run by `make migrate`."""

from __future__ import annotations

import asyncio

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from jobpilot.core.config import get_settings


async def main() -> None:
    """Create LangGraph's checkpoint tables if they don't exist."""
    async with AsyncPostgresSaver.from_conn_string(get_settings().psycopg_dsn) as saver:
        await saver.setup()
    print("checkpointer tables ready")


if __name__ == "__main__":
    asyncio.run(main())
