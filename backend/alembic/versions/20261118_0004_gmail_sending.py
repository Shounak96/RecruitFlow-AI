"""Add encrypted Gmail connection and approved send state.

Revision ID: 20261118_0004
Revises: 20261118_0003
Create Date: 2026-11-18
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261118_0004"
down_revision: str | None = "20261118_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "email_drafts", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "gmail_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("google_subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False),
        sa.Column("is_valid", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("google_subject"),
        sa.UniqueConstraint("owner_id"),
    )
    op.create_table(
        "gmail_oauth_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash"),
    )
    op.create_index("ix_gmail_oauth_states_owner_id", "gmail_oauth_states", ["owner_id"])
    op.create_table(
        "email_sends",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("draft_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("sender_email", sa.String(length=320), nullable=False),
        sa.Column("recipient_email", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("attachment_resume_id", sa.Uuid(), nullable=True),
        sa.Column("attachment_filename", sa.String(length=255), nullable=True),
        sa.Column("attachment_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gmail_message_id", sa.String(length=255), nullable=True),
        sa.Column("gmail_thread_id", sa.String(length=255), nullable=True),
        sa.Column("failure_category", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'uncertain', 'failed', 'stale')",
            name="ck_email_sends_status",
        ),
        sa.ForeignKeyConstraint(["attachment_resume_id"], ["resumes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["draft_id"], ["email_drafts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_sends_owner_id", "email_sends", ["owner_id"])
    op.create_index("ix_email_sends_draft_id", "email_sends", ["draft_id"])
    op.create_index(
        "uq_email_sends_active_draft",
        "email_sends",
        ["draft_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending', 'sending', 'sent', 'uncertain')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_email_sends_active_draft", table_name="email_sends")
    op.drop_index("ix_email_sends_draft_id", table_name="email_sends")
    op.drop_index("ix_email_sends_owner_id", table_name="email_sends")
    op.drop_table("email_sends")
    op.drop_index("ix_gmail_oauth_states_owner_id", table_name="gmail_oauth_states")
    op.drop_table("gmail_oauth_states")
    op.drop_table("gmail_connections")
    op.drop_column("email_drafts", "sent_at")
