"""Remove redundant sender profile owner index.

Revision ID: 20261118_0002
Revises: 20261118_0001
Create Date: 2026-11-18
"""
from collections.abc import Sequence

from alembic import op

revision: str = "20261118_0002"
down_revision: str | None = "20261118_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_sender_profiles_owner_id", table_name="sender_profiles")


def downgrade() -> None:
    op.create_index(
        "ix_sender_profiles_owner_id", "sender_profiles", ["owner_id"], unique=False
    )

