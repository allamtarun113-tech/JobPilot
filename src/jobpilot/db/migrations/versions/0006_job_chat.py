"""job chat

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply migration 0006: add the job chat schema."""
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("tool_called", sa.Text()),
        sa.Column("tier", sa.Integer()),
        sa.Column("proposal_id", sa.BigInteger()),
        sa.Column("version_created", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_chat_messages_job_id", "chat_messages", ["job_id"])
    op.create_table(
        "proposals",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("target_id", sa.BigInteger()),
        sa.Column("target_version", sa.Integer()),
        sa.Column("before", postgresql.JSONB(), nullable=False),
        sa.Column("after", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("callback_token", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("callback_token", name="uq_proposals_callback_token"),
    )
    op.create_index("ix_proposals_job_id", "proposals", ["job_id"])


def downgrade() -> None:
    """Revert migration 0006: remove what upgrade() added."""
    op.drop_index("ix_proposals_job_id", table_name="proposals")
    op.drop_table("proposals")
    op.drop_index("ix_chat_messages_job_id", table_name="chat_messages")
    op.drop_table("chat_messages")
