"""Add local settings, resumes, and email purposes.

Revision ID: 20261118_0001
Revises:
Create Date: 2026-11-18
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261118_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sender_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("gmail_identity_reference", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id"),
    )
    op.create_index("ix_sender_profiles_owner_id", "sender_profiles", ["owner_id"])

    op.create_table(
        "resumes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=36), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_resumes_owner_id", "resumes", ["owner_id"])
    op.create_index(
        "uq_resumes_owner_default",
        "resumes",
        ["owner_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    op.create_table(
        "email_purposes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_purposes_owner_id", "email_purposes", ["owner_id"])
    op.create_index(
        "uq_email_purposes_owner_default",
        "email_purposes",
        ["owner_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )


def downgrade() -> None:
    op.drop_index("uq_email_purposes_owner_default", table_name="email_purposes")
    op.drop_index("ix_email_purposes_owner_id", table_name="email_purposes")
    op.drop_table("email_purposes")
    op.drop_index("uq_resumes_owner_default", table_name="resumes")
    op.drop_index("ix_resumes_owner_id", table_name="resumes")
    op.drop_table("resumes")
    op.drop_index("ix_sender_profiles_owner_id", table_name="sender_profiles")
    op.drop_table("sender_profiles")

