"""jobs.jd_lookup: where the full JD came from, or not_found

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-07 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply migration 0011: add the jobs.jd_lookup schema."""
    op.add_column("jobs", sa.Column("jd_lookup", sa.Text(), nullable=True))


def downgrade() -> None:
    """Revert migration 0011: remove what upgrade() added."""
    op.drop_column("jobs", "jd_lookup")
