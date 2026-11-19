"""Bind automatic-send authorization to an exact attachment.

Revision ID: 20261119_0013
Revises: 20261119_0012
Create Date: 2026-11-19
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261119_0013"
down_revision: str | None = "20261119_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "automatic_send_authorizations",
        sa.Column("attachment_resume_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "automatic_send_authorizations",
        sa.Column("attachment_filename", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "automatic_send_authorizations",
        sa.Column("attachment_size_bytes", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "automatic_send_authorizations",
        sa.Column("attachment_sha256", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "fk_automatic_send_authorizations_attachment_resume_id",
        "automatic_send_authorizations",
        "resumes",
        ["attachment_resume_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_automatic_send_authorizations_attachment_resume_id",
        "automatic_send_authorizations",
        type_="foreignkey",
    )
    op.drop_column("automatic_send_authorizations", "attachment_sha256")
    op.drop_column("automatic_send_authorizations", "attachment_size_bytes")
    op.drop_column("automatic_send_authorizations", "attachment_filename")
    op.drop_column("automatic_send_authorizations", "attachment_resume_id")
