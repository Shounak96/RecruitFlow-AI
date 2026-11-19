"""Add durable idempotency records for one-click generation and sending.

Revision ID: 20261119_0014
Revises: 20261119_0013
Create Date: 2026-11-19
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261119_0014"
down_revision: str | None = "20261119_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "automatic_send_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="generating", nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=True),
        sa.Column("authorization_id", sa.Uuid(), nullable=True),
        sa.Column("send_id", sa.Uuid(), nullable=True),
        sa.Column("failure_category", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('generating', 'dispatching', 'sent', 'uncertain', 'failed')",
            name="ck_automatic_send_requests_status",
        ),
        sa.ForeignKeyConstraint(["draft_id"], ["email_drafts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["authorization_id"], ["automatic_send_authorizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["send_id"], ["email_sends.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "idempotency_key", name="uq_automatic_send_requests_owner_key"
        ),
    )
    op.create_index(
        "ix_automatic_send_requests_owner_id", "automatic_send_requests", ["owner_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_automatic_send_requests_owner_id", table_name="automatic_send_requests")
    op.drop_table("automatic_send_requests")
