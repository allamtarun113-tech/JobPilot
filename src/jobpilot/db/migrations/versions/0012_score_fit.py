"""scores.fit: why you fit / what's missing, built by code (rubric v3)

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-07 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply migration 0012: add the scores.fit schema."""
    op.add_column("scores", sa.Column("fit", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    """Revert migration 0012: remove what upgrade() added."""
    op.drop_column("scores", "fit")
