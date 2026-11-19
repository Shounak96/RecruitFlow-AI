"""Bind send review snapshots to exact attachment bytes.

Revision ID: 20261118_0005
Revises: 20261118_0004
Create Date: 2026-11-18
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261118_0005"
down_revision: str | None = "20261118_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "email_sends",
        sa.Column("attachment_sha256", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("email_sends", "attachment_sha256")
