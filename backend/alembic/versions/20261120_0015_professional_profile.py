"""Add structured professional profile and draft profile snapshots.

Revision ID: 20261120_0015
Revises: 20261119_0014
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261120_0015"
down_revision: str | None = "20261119_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "professional_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("full_name", sa.String(120), server_default="", nullable=False),
        sa.Column("headline", sa.String(200)), sa.Column("email", sa.String(320)),
        sa.Column("location", sa.String(160)), sa.Column("summary", sa.Text()),
        sa.Column("experience_duration", sa.String(120)),
        sa.Column("github_url", sa.String(500)), sa.Column("linkedin_url", sa.String(500)),
        sa.Column("portfolio_url", sa.String(500)),
        sa.Column("custom_links", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("owner_id"),
    )
    op.create_index("ix_professional_profiles_owner_id", "professional_profiles", ["owner_id"])
    for table, columns in (
        ("profile_education", [
            sa.Column("degree", sa.String(160), nullable=False), sa.Column("field", sa.String(160)),
            sa.Column("institution", sa.String(200), nullable=False),
            sa.Column("graduation_status", sa.String(20), server_default="unspecified", nullable=False),
            sa.Column("start_date", sa.Date()), sa.Column("end_date", sa.Date()),
        ]),
        ("profile_experiences", [
            sa.Column("company", sa.String(200), nullable=False), sa.Column("role", sa.String(160), nullable=False),
            sa.Column("employment_type", sa.String(20), server_default="other", nullable=False),
            sa.Column("start_date", sa.Date()), sa.Column("end_date", sa.Date()),
            sa.Column("is_current", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("work_summary", sa.Text(), nullable=False),
            sa.Column("technologies", postgresql.JSONB(), server_default="[]", nullable=False),
        ]),
        ("profile_skill_groups", [
            sa.Column("category", sa.String(100), nullable=False),
            sa.Column("skills", postgresql.JSONB(), server_default="[]", nullable=False),
        ]),
        ("profile_projects", [
            sa.Column("title", sa.String(200), nullable=False), sa.Column("duration", sa.String(120)),
            sa.Column("start_date", sa.Date()), sa.Column("end_date", sa.Date()),
            sa.Column("role", sa.String(160)),
            sa.Column("technologies", postgresql.JSONB(), server_default="[]", nullable=False),
            sa.Column("what_built", sa.Text(), nullable=False), sa.Column("responsibilities", sa.Text()),
            sa.Column("outcomes", sa.Text()), sa.Column("url", sa.String(500)),
        ]),
    ):
        op.create_table(
            table,
            sa.Column("id", sa.Uuid(), nullable=False), sa.Column("owner_id", sa.Uuid(), nullable=False),
            sa.Column("profile_id", sa.Uuid(), nullable=False), sa.Column("position", sa.Integer(), nullable=False),
            *columns,
            sa.ForeignKeyConstraint(["profile_id"], ["professional_profiles.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            *( [
                sa.CheckConstraint("position >= 0 AND position < 5", name="ck_profile_projects_max_position"),
                sa.UniqueConstraint("profile_id", "position", name="uq_profile_projects_position"),
            ] if table == "profile_projects" else [] ),
        )
        op.create_index(f"ix_{table}_owner_id", table, ["owner_id"])
        op.create_index(f"ix_{table}_profile_id", table, ["profile_id"])
    op.add_column("email_drafts", sa.Column("profile_fingerprint", sa.String(64)))
    op.add_column("email_drafts", sa.Column("included_profile_links", postgresql.JSONB(), server_default="{}", nullable=False))


def downgrade() -> None:
    op.drop_column("email_drafts", "included_profile_links")
    op.drop_column("email_drafts", "profile_fingerprint")
    for table in ("profile_projects", "profile_skill_groups", "profile_experiences", "profile_education"):
        op.drop_table(table)
    op.drop_index("ix_professional_profiles_owner_id", table_name="professional_profiles")
    op.drop_table("professional_profiles")
