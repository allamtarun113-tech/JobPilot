"""Integrity: foreign keys, email lock version, pause/snooze/outcome fields, prompt+model
provenance on generated rows, and the jobs-API usage ledger.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-05 20:00:53.761611
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply migration 0008: add the Integrity schema."""
    op.create_table(
        "api_usage",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_usage")),
    )
    op.create_index("ix_api_usage_created_at", "api_usage", ["created_at"], unique=False)
    op.create_foreign_key(op.f("fk_approvals_job_id_jobs"), "approvals", "jobs", ["job_id"], ["id"])
    op.add_column("cards", sa.Column("outcome_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("cards", sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("cards", sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(op.f("fk_cards_job_id_jobs"), "cards", "jobs", ["job_id"], ["id"])
    op.create_foreign_key(
        op.f("fk_cards_approved_resume_version_id_resume_versions"),
        "cards",
        "resume_versions",
        ["approved_resume_version_id"],
        ["id"],
    )
    op.add_column("chat_messages", sa.Column("prompt_version", sa.Text(), nullable=True))
    op.add_column("chat_messages", sa.Column("model_id", sa.Text(), nullable=True))
    op.create_foreign_key(op.f("fk_chat_messages_job_id_jobs"), "chat_messages", "jobs", ["job_id"], ["id"])
    op.create_foreign_key(
        op.f("fk_chat_messages_proposal_id_proposals"), "chat_messages", "proposals", ["proposal_id"], ["id"]
    )
    op.add_column("emails", sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False))
    op.add_column("emails", sa.Column("prompt_version", sa.Text(), nullable=True))
    op.add_column("emails", sa.Column("model_id", sa.Text(), nullable=True))
    op.create_foreign_key(op.f("fk_emails_job_id_jobs"), "emails", "jobs", ["job_id"], ["id"])
    op.create_foreign_key(
        op.f("fk_emails_resume_version_id_resume_versions"),
        "emails",
        "resume_versions",
        ["resume_version_id"],
        ["id"],
    )
    op.create_foreign_key(op.f("fk_job_sources_job_id_jobs"), "job_sources", "jobs", ["job_id"], ["id"])
    op.create_foreign_key(op.f("fk_llm_usage_run_id_runs"), "llm_usage", "runs", ["run_id"], ["id"])
    op.create_index("ix_outbox_external_id", "outbox", ["external_id"], unique=False)
    op.add_column("prep_sets", sa.Column("prompt_version", sa.Text(), nullable=True))
    op.add_column("prep_sets", sa.Column("model_id", sa.Text(), nullable=True))
    op.create_foreign_key(op.f("fk_prep_sets_job_id_jobs"), "prep_sets", "jobs", ["job_id"], ["id"])
    op.create_foreign_key(op.f("fk_proposals_job_id_jobs"), "proposals", "jobs", ["job_id"], ["id"])
    # queue.job_id is a jobs.id, never generated: drop the SERIAL default the first migration created.
    op.execute("ALTER TABLE queue ALTER COLUMN job_id DROP DEFAULT")
    op.execute("DROP SEQUENCE IF EXISTS queue_job_id_seq")
    op.create_foreign_key(op.f("fk_queue_job_id_jobs"), "queue", "jobs", ["job_id"], ["id"])
    op.add_column("resume_versions", sa.Column("prompt_version", sa.Text(), nullable=True))
    op.add_column("resume_versions", sa.Column("model_id", sa.Text(), nullable=True))
    op.create_foreign_key(
        op.f("fk_resume_versions_base_resume_id_resume_doc"),
        "resume_versions",
        "resume_doc",
        ["base_resume_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("fk_resume_versions_job_id_jobs"), "resume_versions", "jobs", ["job_id"], ["id"]
    )
    op.add_column(
        "runs", sa.Column("pause_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False)
    )
    op.create_foreign_key(
        op.f("fk_scores_requirements_id_requirements"), "scores", "requirements", ["requirements_id"], ["id"]
    )
    op.create_foreign_key(op.f("fk_scores_job_id_jobs"), "scores", "jobs", ["job_id"], ["id"])
    op.create_foreign_key(
        op.f("fk_sent_emails_email_id_emails"), "sent_emails", "emails", ["email_id"], ["id"]
    )
    op.create_foreign_key(op.f("fk_sent_emails_job_id_jobs"), "sent_emails", "jobs", ["job_id"], ["id"])
    op.create_foreign_key(
        op.f("fk_shared_jobs_event_id_events"), "shared_jobs", "events", ["event_id"], ["id"]
    )
    op.create_foreign_key(op.f("fk_shared_jobs_job_id_jobs"), "shared_jobs", "jobs", ["job_id"], ["id"])


def downgrade() -> None:
    """Revert migration 0008: remove what upgrade() added."""
    op.drop_constraint(op.f("fk_shared_jobs_job_id_jobs"), "shared_jobs", type_="foreignkey")
    op.drop_constraint(op.f("fk_shared_jobs_event_id_events"), "shared_jobs", type_="foreignkey")
    op.drop_constraint(op.f("fk_sent_emails_job_id_jobs"), "sent_emails", type_="foreignkey")
    op.drop_constraint(op.f("fk_sent_emails_email_id_emails"), "sent_emails", type_="foreignkey")
    op.drop_constraint(op.f("fk_scores_job_id_jobs"), "scores", type_="foreignkey")
    op.drop_constraint(op.f("fk_scores_requirements_id_requirements"), "scores", type_="foreignkey")
    op.drop_column("runs", "pause_requested")
    op.drop_constraint(op.f("fk_resume_versions_job_id_jobs"), "resume_versions", type_="foreignkey")
    op.drop_constraint(
        op.f("fk_resume_versions_base_resume_id_resume_doc"), "resume_versions", type_="foreignkey"
    )
    op.drop_column("resume_versions", "model_id")
    op.drop_column("resume_versions", "prompt_version")
    op.drop_constraint(op.f("fk_queue_job_id_jobs"), "queue", type_="foreignkey")
    op.drop_constraint(op.f("fk_proposals_job_id_jobs"), "proposals", type_="foreignkey")
    op.drop_constraint(op.f("fk_prep_sets_job_id_jobs"), "prep_sets", type_="foreignkey")
    op.drop_column("prep_sets", "model_id")
    op.drop_column("prep_sets", "prompt_version")
    op.drop_index("ix_outbox_external_id", table_name="outbox")
    op.drop_constraint(op.f("fk_llm_usage_run_id_runs"), "llm_usage", type_="foreignkey")
    op.drop_constraint(op.f("fk_job_sources_job_id_jobs"), "job_sources", type_="foreignkey")
    op.drop_constraint(op.f("fk_emails_resume_version_id_resume_versions"), "emails", type_="foreignkey")
    op.drop_constraint(op.f("fk_emails_job_id_jobs"), "emails", type_="foreignkey")
    op.drop_column("emails", "model_id")
    op.drop_column("emails", "prompt_version")
    op.drop_column("emails", "lock_version")
    op.drop_constraint(op.f("fk_chat_messages_proposal_id_proposals"), "chat_messages", type_="foreignkey")
    op.drop_constraint(op.f("fk_chat_messages_job_id_jobs"), "chat_messages", type_="foreignkey")
    op.drop_column("chat_messages", "model_id")
    op.drop_column("chat_messages", "prompt_version")
    op.drop_constraint(
        op.f("fk_cards_approved_resume_version_id_resume_versions"), "cards", type_="foreignkey"
    )
    op.drop_constraint(op.f("fk_cards_job_id_jobs"), "cards", type_="foreignkey")
    op.drop_column("cards", "reminded_at")
    op.drop_column("cards", "snoozed_until")
    op.drop_column("cards", "outcome_at")
    op.drop_constraint(op.f("fk_approvals_job_id_jobs"), "approvals", type_="foreignkey")
    op.drop_index("ix_api_usage_created_at", table_name="api_usage")
    op.drop_table("api_usage")
