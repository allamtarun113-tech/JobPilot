"""cards and tailored resume versions

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply migration 0004: add the cards and tailored resume versions schema."""
    op.create_table(
        "cards",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), server_default="new", nullable=False),
        sa.Column("callback_token", sa.Text(), nullable=False),
        sa.Column("approved_resume_version_id", sa.BigInteger()),
        sa.Column("telegram_outbox_id", sa.BigInteger()),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.Text()),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cards")),
        sa.UniqueConstraint("job_id", name=op.f("uq_cards_job_id")),
        sa.UniqueConstraint("callback_token", name=op.f("uq_cards_callback_token")),
    )
    op.create_index("ix_cards_status", "cards", ["status"])
    op.create_table(
        "resume_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("base_resume_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("doc", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "operations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("pdf_key", sa.Text(), nullable=False),
        sa.Column("parse_ok", sa.Boolean(), nullable=False),
        sa.Column("parse_report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("keyword_coverage", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("diff", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resume_versions")),
        sa.UniqueConstraint("job_id", "version", name=op.f("uq_resume_versions_job_id_version")),
    )
    op.create_index("ix_resume_versions_job_id", "resume_versions", ["job_id"])


def downgrade() -> None:
    """Revert migration 0004: remove what upgrade() added."""
    op.drop_index("ix_resume_versions_job_id", table_name="resume_versions")
    op.drop_table("resume_versions")
    op.drop_index("ix_cards_status", table_name="cards")
    op.drop_table("cards")
