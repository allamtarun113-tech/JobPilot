"""Per-job Job Chat history."""

from sqlalchemy.engine import RowMapping

from jobpilot.db.queries import chat_messages
from jobpilot.db.session import transaction


async def load(job_id: int) -> list[RowMapping]:
    """Return a job's recent chat messages, oldest first."""
    async with transaction() as conn:
        return await chat_messages.for_job(conn, job_id)


async def remember(job_id: int, role: str, text: str, **metadata: object) -> int:
    """Store one chat message (and what it did) for a job; return its id."""
    async with transaction() as conn:
        return await chat_messages.add(conn, job_id=job_id, role=role, text=text, **metadata)
