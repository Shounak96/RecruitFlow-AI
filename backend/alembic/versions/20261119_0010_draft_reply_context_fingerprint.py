"""Bind reply drafts to the exact received-message content.

Revision ID: 20261119_0010
Revises: 20261119_0009
Create Date: 2026-11-19
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261119_0010"
down_revision: str | None = "20261119_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "email_drafts",
        sa.Column("reply_context_fingerprint", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("email_drafts", "reply_context_fingerprint")
