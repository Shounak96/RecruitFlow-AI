"""Consolidate legacy project details into one description.

Revision ID: 20261121_0016
Revises: 20261120_0015
"""
from collections.abc import Mapping, Sequence
from typing import Any

from alembic import op
import sqlalchemy as sa

revision: str = "20261121_0016"
down_revision: str | None = "20261120_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def merge_legacy_project_description(values: Mapping[str, Any]) -> str:
    """Merge distinct legacy details without replacing existing description text."""
    description = str(values.get("description") or "").strip()

    def append(label: str, raw: Any) -> None:
        nonlocal description
        value = str(raw or "").strip()
        if not value or value.casefold() in description.casefold():
            return
        line = f"{label}: {value}"
        description = f"{description}\n\n{line}" if description else line

    append("What was built", values.get("what_built"))
    append("Role", values.get("role"))
    append("Duration", values.get("duration"))
    start = values.get("start_date")
    end = values.get("end_date")
    if start or end:
        append("Dates", f"{start or 'unspecified'} to {end or 'unspecified'}")
    append("Responsibilities", values.get("responsibilities"))
    append("Outcomes", values.get("outcomes"))
    append("Repository/demo", values.get("url"))
    return description


def upgrade() -> None:
    op.add_column("profile_projects", sa.Column("description", sa.Text(), nullable=True))
    projects = sa.table(
        "profile_projects",
        sa.column("id", sa.Uuid()), sa.column("description", sa.Text()),
        sa.column("what_built", sa.Text()), sa.column("role", sa.String()),
        sa.column("duration", sa.String()), sa.column("start_date", sa.Date()),
        sa.column("end_date", sa.Date()), sa.column("responsibilities", sa.Text()),
        sa.column("outcomes", sa.Text()), sa.column("url", sa.String()),
    )
    connection = op.get_bind()
    rows = connection.execute(sa.select(projects)).mappings()
    for row in rows:
        connection.execute(
            projects.update().where(projects.c.id == row["id"]).values(
                description=merge_legacy_project_description(row)
            )
        )
    op.alter_column("profile_projects", "description", existing_type=sa.Text(), nullable=False)
    op.alter_column("profile_projects", "what_built", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    # Preserve the complete consolidated/user-edited description in the only
    # required legacy text column before removing the new field.
    op.execute("UPDATE profile_projects SET what_built = description")
    op.alter_column("profile_projects", "what_built", existing_type=sa.Text(), nullable=False)
    op.drop_column("profile_projects", "description")
