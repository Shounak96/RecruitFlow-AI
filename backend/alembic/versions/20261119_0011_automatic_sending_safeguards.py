"""Add Stage A automatic-sending preferences and authorization safeguards.

Revision ID: 20261119_0011
Revises: 20261119_0010
Create Date: 2026-11-19
"""
from collections.abc import Sequence
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "20261119_0011"
down_revision: str | None = "20261119_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sending_preferences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("sending_mode", sa.String(length=20), server_default="manual", nullable=False),
        sa.Column("automatic_initial_outreach", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("automatic_followups", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("daily_send_limit", sa.Integer(), server_default="1", nullable=False),
        sa.Column("automation_consented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("automation_disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("sending_mode IN ('manual', 'automatic')", name="ck_sending_preferences_mode"),
        sa.CheckConstraint("daily_send_limit IN (1, 3, 5, 10)", name="ck_sending_preferences_daily_limit"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id"),
    )
    bind = op.get_bind()
    owners = list(bind.execute(sa.text("SELECT owner_id FROM sender_profiles")).scalars())
    for owner_id in owners:
        bind.execute(
            sa.text(
                "INSERT INTO sending_preferences "
                "(id, owner_id, sending_mode, automatic_initial_outreach, "
                "automatic_followups, daily_send_limit) "
                "VALUES (:id, :owner_id, 'manual', false, false, 1)"
            ),
            {"id": uuid4(), "owner_id": owner_id},
        )

    op.create_table(
        "automatic_send_authorizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_kind", sa.String(length=20), nullable=False),
        sa.Column("recipient_email", sa.String(length=320), nullable=False),
        sa.Column("sender_email", sa.String(length=320), nullable=False),
        sa.Column("gmail_subject", sa.String(length=255), nullable=False),
        sa.Column("draft_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("authorization_kind IN ('initial', 'follow_up')", name="ck_automatic_send_authorizations_kind"),
        sa.ForeignKeyConstraint(["draft_id"], ["email_drafts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_automatic_send_authorizations_owner_id", "automatic_send_authorizations", ["owner_id"])
    op.create_index("ix_automatic_send_authorizations_draft_id", "automatic_send_authorizations", ["draft_id"])
    op.create_index(
        "uq_automatic_send_authorizations_active_draft",
        "automatic_send_authorizations",
        ["draft_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "automatic_send_claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("claim_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="claimed", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('claimed', 'sent', 'uncertain', 'released')", name="ck_automatic_send_claims_status"),
        sa.ForeignKeyConstraint(["authorization_id"], ["automatic_send_authorizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["draft_id"], ["email_drafts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("authorization_id", name="uq_automatic_send_claims_authorization"),
    )
    op.create_index("ix_automatic_send_claims_owner_id", "automatic_send_claims", ["owner_id"])
    op.create_index("ix_automatic_send_claims_draft_id", "automatic_send_claims", ["draft_id"])
    op.create_index("ix_automatic_send_claims_owner_day", "automatic_send_claims", ["owner_id", "claim_date"])


def downgrade() -> None:
    op.drop_index("ix_automatic_send_claims_owner_day", table_name="automatic_send_claims")
    op.drop_index("ix_automatic_send_claims_draft_id", table_name="automatic_send_claims")
    op.drop_index("ix_automatic_send_claims_owner_id", table_name="automatic_send_claims")
    op.drop_table("automatic_send_claims")
    op.drop_index("uq_automatic_send_authorizations_active_draft", table_name="automatic_send_authorizations")
    op.drop_index("ix_automatic_send_authorizations_draft_id", table_name="automatic_send_authorizations")
    op.drop_index("ix_automatic_send_authorizations_owner_id", table_name="automatic_send_authorizations")
    op.drop_table("automatic_send_authorizations")
    op.drop_table("sending_preferences")
