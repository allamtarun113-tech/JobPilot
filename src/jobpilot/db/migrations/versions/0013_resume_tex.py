"""resume_versions.tex and rounds: per-job LaTeX copies and their edit rounds (resume v2)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-07 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply migration 0013: add the resume_versions.tex and rounds schema."""
    op.add_column("resume_versions", sa.Column("tex", sa.Text(), nullable=True))
    op.add_column(
        "resume_versions", sa.Column("rounds", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    """Revert migration 0013: remove what upgrade() added."""
    op.drop_column("resume_versions", "rounds")
    op.drop_column("resume_versions", "tex")
