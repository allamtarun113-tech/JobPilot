"""outreach emails

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply migration 0005: add the outreach emails schema."""
    op.create_table(
        "emails",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("resume_version_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("to_address", sa.Text(), nullable=False),
        sa.Column("to_source", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(), nullable=False),
        sa.Column("gmail_draft_id", sa.Text()),
        sa.Column("status", sa.Text(), server_default="draft", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("job_id", "version", name="uq_emails_job_id_version"),
    )
    op.create_index("ix_emails_job_id", "emails", ["job_id"])
    op.create_table(
        "sent_emails",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("email_id", sa.BigInteger(), nullable=False),
        sa.Column("gmail_message_id", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("job_id", name="uq_sent_emails_job_id"),
    )


def downgrade() -> None:
    """Revert migration 0005: remove what upgrade() added."""
    op.drop_table("sent_emails")
    op.drop_index("ix_emails_job_id", table_name="emails")
    op.drop_table("emails")
