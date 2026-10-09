"""email thread id

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-05 22:36:28.825815
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply migration 0010: add the email thread id schema."""
    op.add_column("emails", sa.Column("gmail_thread_id", sa.Text(), nullable=True))


def downgrade() -> None:
    """Revert migration 0010: remove what upgrade() added."""
    op.drop_column("emails", "gmail_thread_id")
