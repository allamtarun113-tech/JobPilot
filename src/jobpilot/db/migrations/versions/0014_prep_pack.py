"""prep_sets.pack: skill recap, resources and build stats (prep v2)

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-07 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply migration 0014: add the prep_sets.pack schema."""
    op.add_column("prep_sets", sa.Column("pack", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    """Revert migration 0014: remove what upgrade() added."""
    op.drop_column("prep_sets", "pack")
