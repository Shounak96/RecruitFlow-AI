"""Allow custom bounded automatic-send daily limits.

Revision ID: 20261119_0012
Revises: 20261119_0011
Create Date: 2026-11-19
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261119_0012"
down_revision: str | None = "20261119_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_sending_preferences_daily_limit",
        "sending_preferences",
        type_="check",
    )
    op.create_check_constraint(
        "ck_sending_preferences_daily_limit",
        "sending_preferences",
        "daily_send_limit >= 1 AND daily_send_limit <= 500",
    )
    op.alter_column(
        "sending_preferences",
        "daily_send_limit",
        existing_type=sa.Integer(),
        server_default="5",
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "sending_preferences",
        "daily_send_limit",
        existing_type=sa.Integer(),
        server_default="1",
        existing_nullable=False,
    )
    op.drop_constraint(
        "ck_sending_preferences_daily_limit",
        "sending_preferences",
        type_="check",
    )
    op.create_check_constraint(
        "ck_sending_preferences_daily_limit",
        "sending_preferences",
        "daily_send_limit IN (1, 3, 5, 10)",
    )
