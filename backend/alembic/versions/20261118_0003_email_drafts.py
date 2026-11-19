"""Add editable email drafts.

Revision ID: 20261118_0003
Revises: 20261118_0002
Create Date: 2026-11-18
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261118_0003"
down_revision: str | None = "20261118_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("purpose_id", sa.Uuid(), nullable=False),
        sa.Column("resume_id", sa.Uuid(), nullable=True),
        sa.Column("recipient_name", sa.String(length=160), nullable=False),
        sa.Column("recipient_email", sa.String(length=320), nullable=False),
        sa.Column("company", sa.String(length=160), nullable=True),
        sa.Column("role", sa.String(length=160), nullable=True),
        sa.Column("job_description", sa.Text(), nullable=True),
        sa.Column("attach_resume", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["purpose_id"], ["email_purposes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_drafts_owner_id", "email_drafts", ["owner_id"])
    op.create_index("ix_email_drafts_purpose_id", "email_drafts", ["purpose_id"])
    op.create_index("ix_email_drafts_resume_id", "email_drafts", ["resume_id"])


def downgrade() -> None:
    op.drop_index("ix_email_drafts_resume_id", table_name="email_drafts")
    op.drop_index("ix_email_drafts_purpose_id", table_name="email_drafts")
    op.drop_index("ix_email_drafts_owner_id", table_name="email_drafts")
    op.drop_table("email_drafts")

