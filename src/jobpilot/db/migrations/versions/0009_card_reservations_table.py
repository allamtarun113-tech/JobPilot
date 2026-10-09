"""card reservations table

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-05 21:35:47.124075
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply migration 0009: add the card reservations table schema."""
    op.create_table(
        "card_reservations",
        sa.Column("job_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("user_id", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("existing_card", sa.Boolean(), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name=op.f("fk_card_reservations_job_id_jobs")),
        sa.PrimaryKeyConstraint("job_id", name=op.f("pk_card_reservations")),
    )


def downgrade() -> None:
    """Revert migration 0009: remove what upgrade() added."""
    op.drop_table("card_reservations")
