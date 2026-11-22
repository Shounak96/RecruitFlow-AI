"""Bind AI reply drafts and reviews to received Gmail messages.

Revision ID: 20261119_0009
Revises: 20261119_0008
Create Date: 2026-11-19
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261119_0009"
down_revision: str | None = "20261119_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "email_drafts", sa.Column("reply_to_received_message_id", sa.Uuid(), nullable=True)
    )
    op.create_index(
        "ix_email_drafts_reply_to_received_message_id",
        "email_drafts",
        ["reply_to_received_message_id"],
    )
    op.create_index(
        "uq_email_drafts_active_received_reply",
        "email_drafts",
        ["reply_to_received_message_id"],
        unique=True,
        postgresql_where=sa.text(
            "reply_to_received_message_id IS NOT NULL AND sent_at IS NULL AND deleted_at IS NULL"
        ),
    )
    op.create_foreign_key(
        "fk_email_drafts_reply_to_received_message_id",
        "email_drafts",
        "received_gmail_messages",
        ["reply_to_received_message_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "email_sends", sa.Column("reply_to_received_message_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "email_sends", sa.Column("reply_context_fingerprint", sa.String(length=64), nullable=True)
    )
    op.create_index(
        "ix_email_sends_reply_to_received_message_id",
        "email_sends",
        ["reply_to_received_message_id"],
    )
    op.create_foreign_key(
        "fk_email_sends_reply_to_received_message_id",
        "email_sends",
        "received_gmail_messages",
        ["reply_to_received_message_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_email_sends_reply_to_received_message_id", "email_sends", type_="foreignkey"
    )
    op.drop_index("ix_email_sends_reply_to_received_message_id", table_name="email_sends")
    op.drop_column("email_sends", "reply_context_fingerprint")
    op.drop_column("email_sends", "reply_to_received_message_id")
    op.drop_constraint(
        "fk_email_drafts_reply_to_received_message_id", "email_drafts", type_="foreignkey"
    )
    op.drop_index("uq_email_drafts_active_received_reply", table_name="email_drafts")
    op.drop_index("ix_email_drafts_reply_to_received_message_id", table_name="email_drafts")
    op.drop_column("email_drafts", "reply_to_received_message_id")
