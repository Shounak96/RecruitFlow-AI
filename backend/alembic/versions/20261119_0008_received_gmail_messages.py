"""Persist safely parsed Gmail replies.

Revision ID: 20261119_0008
Revises: 20261119_0007
Create Date: 2026-11-19
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261119_0008"
down_revision: str | None = "20261119_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "received_gmail_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("gmail_message_id", sa.String(length=255), nullable=False),
        sa.Column("gmail_thread_id", sa.String(length=255), nullable=False),
        sa.Column("sender_name", sa.String(length=160), nullable=True),
        sa.Column("sender_email", sa.String(length=320), nullable=False),
        sa.Column("recipient_email", sa.String(length=320), nullable=True),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rfc_message_id", sa.String(length=320), nullable=True),
        sa.Column("in_reply_to", sa.String(length=320), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["email_sends.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "gmail_message_id", name="uq_received_gmail_messages_owner_message"
        ),
    )
    op.create_index("ix_received_gmail_messages_owner_id", "received_gmail_messages", ["owner_id"])
    op.create_index("ix_received_gmail_messages_conversation_id", "received_gmail_messages", ["conversation_id"])
    op.create_index("ix_received_gmail_messages_gmail_thread_id", "received_gmail_messages", ["gmail_thread_id"])
    op.create_index("ix_received_gmail_messages_received_at", "received_gmail_messages", ["received_at"])


def downgrade() -> None:
    op.drop_index("ix_received_gmail_messages_received_at", table_name="received_gmail_messages")
    op.drop_index("ix_received_gmail_messages_gmail_thread_id", table_name="received_gmail_messages")
    op.drop_index("ix_received_gmail_messages_conversation_id", table_name="received_gmail_messages")
    op.drop_index("ix_received_gmail_messages_owner_id", table_name="received_gmail_messages")
    op.drop_table("received_gmail_messages")
