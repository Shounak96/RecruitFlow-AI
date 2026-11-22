"""Add durable local deletion tombstones.

Revision ID: 20261119_0007
Revises: 20261118_0006
Create Date: 2026-11-19
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261119_0007"
down_revision: str | None = "20261118_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "email_drafts", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "email_sends", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("email_sends", "deleted_at")
    op.drop_column("email_drafts", "deleted_at")
