"""Add immutable conversation and follow-up threading metadata.

Revision ID: 20261118_0006
Revises: 20261118_0005
Create Date: 2026-11-18
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261118_0006"
down_revision: str | None = "20261118_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("email_sends", sa.Column("conversation_id", sa.Uuid(), nullable=True))
    op.add_column("email_sends", sa.Column("parent_send_id", sa.Uuid(), nullable=True))
    op.add_column("email_sends", sa.Column("sender_name", sa.String(120), nullable=True))
    op.add_column("email_sends", sa.Column("rfc_message_id", sa.String(320), nullable=True))
    op.add_column(
        "email_sends", sa.Column("intended_gmail_thread_id", sa.String(255), nullable=True)
    )
    op.add_column("email_sends", sa.Column("in_reply_to", sa.String(320), nullable=True))
    op.add_column("email_sends", sa.Column("references_header", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_email_sends_conversation_id", "email_sends", "email_sends",
        ["conversation_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_foreign_key(
        "fk_email_sends_parent_send_id", "email_sends", "email_sends",
        ["parent_send_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_index("ix_email_sends_conversation_id", "email_sends", ["conversation_id"])
    op.create_index(
        "uq_email_sends_active_conversation", "email_sends", ["conversation_id"],
        unique=True,
        postgresql_where=sa.text(
            "conversation_id IS NOT NULL AND status IN ('pending', 'sending', 'uncertain')"
        ),
    )
    op.execute(
        "UPDATE email_sends AS es SET sender_name = sp.name "
        "FROM sender_profiles AS sp WHERE es.owner_id = sp.owner_id"
    )
    op.add_column("email_drafts", sa.Column("conversation_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_email_drafts_conversation_id", "email_drafts", "email_sends",
        ["conversation_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_index("ix_email_drafts_conversation_id", "email_drafts", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_email_drafts_conversation_id", table_name="email_drafts")
    op.drop_constraint("fk_email_drafts_conversation_id", "email_drafts", type_="foreignkey")
    op.drop_column("email_drafts", "conversation_id")
    op.drop_index("uq_email_sends_active_conversation", table_name="email_sends")
    op.drop_index("ix_email_sends_conversation_id", table_name="email_sends")
    op.drop_constraint("fk_email_sends_parent_send_id", "email_sends", type_="foreignkey")
    op.drop_constraint("fk_email_sends_conversation_id", "email_sends", type_="foreignkey")
    op.drop_column("email_sends", "references_header")
    op.drop_column("email_sends", "in_reply_to")
    op.drop_column("email_sends", "intended_gmail_thread_id")
    op.drop_column("email_sends", "rfc_message_id")
    op.drop_column("email_sends", "sender_name")
    op.drop_column("email_sends", "parent_send_id")
    op.drop_column("email_sends", "conversation_id")
