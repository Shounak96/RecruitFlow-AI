from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SenderProfile(Base):
    __tablename__ = "sender_profiles"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(unique=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    gmail_identity_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ProfessionalProfile(Base):
    __tablename__ = "professional_profiles"
    __table_args__ = (
        UniqueConstraint("owner_id", name="professional_profiles_owner_id_key"),
        Index("ix_professional_profiles_owner_id", "owner_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column()
    full_name: Mapped[str] = mapped_column(String(120), default="", server_default="")
    headline: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    location: Mapped[str | None] = mapped_column(String(160), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    experience_duration: Mapped[str | None] = mapped_column(String(120), nullable=True)
    github_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    portfolio_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    custom_links: Mapped[list[dict]] = mapped_column(JSONB, default=list, server_default="[]")
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProfileEducation(Base):
    __tablename__ = "profile_education"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    profile_id: Mapped[UUID] = mapped_column(ForeignKey("professional_profiles.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(default=0)
    degree: Mapped[str] = mapped_column(String(160))
    field: Mapped[str | None] = mapped_column(String(160), nullable=True)
    institution: Mapped[str] = mapped_column(String(200))
    graduation_status: Mapped[str] = mapped_column(String(20), default="unspecified")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class ProfileExperience(Base):
    __tablename__ = "profile_experiences"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    profile_id: Mapped[UUID] = mapped_column(ForeignKey("professional_profiles.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(default=0)
    company: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(160))
    employment_type: Mapped[str] = mapped_column(String(20), default="other")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    work_summary: Mapped[str] = mapped_column(Text)
    technologies: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")


class ProfileSkillGroup(Base):
    __tablename__ = "profile_skill_groups"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    profile_id: Mapped[UUID] = mapped_column(ForeignKey("professional_profiles.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(default=0)
    category: Mapped[str] = mapped_column(String(100))
    skills: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")


class ProfileProject(Base):
    __tablename__ = "profile_projects"
    __table_args__ = (
        CheckConstraint("position >= 0 AND position < 5", name="ck_profile_projects_max_position"),
        UniqueConstraint("profile_id", "position", name="uq_profile_projects_position"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    profile_id: Mapped[UUID] = mapped_column(ForeignKey("professional_profiles.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column()
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    # Legacy fields are retained for the staged 0016 migration and can be dropped later.
    duration: Mapped[str | None] = mapped_column(String(120), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    role: Mapped[str | None] = mapped_column(String(160), nullable=True)
    technologies: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    what_built: Mapped[str | None] = mapped_column(Text, nullable=True)
    responsibilities: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcomes: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)


class Resume(Base):
    __tablename__ = "resumes"
    __table_args__ = (
        Index(
            "uq_resumes_owner_default",
            "owner_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(36), unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class EmailPurpose(Base):
    __tablename__ = "email_purposes"
    __table_args__ = (
        Index(
            "uq_email_purposes_owner_default",
            "owner_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EmailDraft(Base):
    __tablename__ = "email_drafts"
    __table_args__ = (
        Index(
            "uq_email_drafts_active_received_reply",
            "reply_to_received_message_id",
            unique=True,
            postgresql_where=text(
                "reply_to_received_message_id IS NOT NULL AND sent_at IS NULL AND deleted_at IS NULL"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    purpose_id: Mapped[UUID] = mapped_column(
        ForeignKey("email_purposes.id", ondelete="RESTRICT"), index=True
    )
    resume_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "email_sends.id",
            ondelete="RESTRICT",
            name="fk_email_drafts_conversation_id",
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )
    reply_to_received_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "received_gmail_messages.id",
            ondelete="RESTRICT",
            name="fk_email_drafts_reply_to_received_message_id",
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )
    reply_context_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    profile_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    included_profile_links: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    recipient_name: Mapped[str] = mapped_column(String(160))
    recipient_email: Mapped[str] = mapped_column(String(320))
    company: Mapped[str | None] = mapped_column(String(160), nullable=True)
    role: Mapped[str | None] = mapped_column(String(160), nullable=True)
    job_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    attach_resume: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def status(self) -> str:
        return "sent" if self.sent_at is not None else "not_sent"


class GmailConnection(Base):
    __tablename__ = "gmail_connections"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(unique=True)
    google_subject: Mapped[str] = mapped_column(String(255), unique=True)
    email: Mapped[str] = mapped_column(String(320))
    encrypted_access_token: Mapped[str] = mapped_column(Text)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text)
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[str] = mapped_column(Text)
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GmailOAuthState(Base):
    __tablename__ = "gmail_oauth_states"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EmailSend(Base):
    __tablename__ = "email_sends"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'uncertain', 'failed', 'stale')",
            name="ck_email_sends_status",
        ),
        Index(
            "uq_email_sends_active_draft",
            "draft_id",
            unique=True,
            postgresql_where=text(
                "status IN ('pending', 'sending', 'sent', 'uncertain')"
            ),
        ),
        Index(
            "uq_email_sends_active_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text(
                "conversation_id IS NOT NULL AND "
                "status IN ('pending', 'sending', 'uncertain')"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("email_drafts.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "email_sends.id", ondelete="RESTRICT", name="fk_email_sends_conversation_id"
        ),
        nullable=True,
        index=True,
    )
    parent_send_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "email_sends.id", ondelete="RESTRICT", name="fk_email_sends_parent_send_id"
        ),
        nullable=True,
    )
    reply_to_received_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "received_gmail_messages.id",
            ondelete="RESTRICT",
            name="fk_email_sends_reply_to_received_message_id",
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )
    reply_context_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")
    draft_fingerprint: Mapped[str] = mapped_column(String(64))
    sender_email: Mapped[str] = mapped_column(String(320))
    sender_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    recipient_email: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    attachment_resume_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True
    )
    attachment_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attachment_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    attachment_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gmail_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rfc_message_id: Mapped[str | None] = mapped_column(String(320), nullable=True)
    intended_gmail_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(320), nullable=True)
    references_header: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ReceivedGmailMessage(Base):
    __tablename__ = "received_gmail_messages"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "gmail_message_id", name="uq_received_gmail_messages_owner_message"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("email_sends.id", ondelete="RESTRICT"), index=True
    )
    gmail_message_id: Mapped[str] = mapped_column(String(255))
    gmail_thread_id: Mapped[str] = mapped_column(String(255), index=True)
    sender_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    sender_email: Mapped[str] = mapped_column(String(320))
    recipient_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(200), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    rfc_message_id: Mapped[str | None] = mapped_column(String(320), nullable=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(320), nullable=True)
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SendingPreference(Base):
    __tablename__ = "sending_preferences"
    __table_args__ = (
        CheckConstraint(
            "sending_mode IN ('manual', 'automatic')",
            name="ck_sending_preferences_mode",
        ),
        CheckConstraint(
            "daily_send_limit >= 1 AND daily_send_limit <= 500",
            name="ck_sending_preferences_daily_limit",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(unique=True)
    sending_mode: Mapped[str] = mapped_column(
        String(20), default="manual", server_default="manual"
    )
    automatic_initial_outreach: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    automatic_followups: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    daily_send_limit: Mapped[int] = mapped_column(default=5, server_default="5")
    automation_consented_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    automation_disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AutomaticSendAuthorization(Base):
    __tablename__ = "automatic_send_authorizations"
    __table_args__ = (
        CheckConstraint(
            "authorization_kind IN ('initial', 'follow_up')",
            name="ck_automatic_send_authorizations_kind",
        ),
        Index(
            "uq_automatic_send_authorizations_active_draft",
            "draft_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("email_drafts.id", ondelete="RESTRICT"), index=True
    )
    authorization_kind: Mapped[str] = mapped_column(String(20))
    recipient_email: Mapped[str] = mapped_column(String(320))
    sender_email: Mapped[str] = mapped_column(String(320))
    gmail_subject: Mapped[str] = mapped_column(String(255))
    draft_fingerprint: Mapped[str] = mapped_column(String(64))
    attachment_resume_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="RESTRICT"), nullable=True
    )
    attachment_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attachment_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    attachment_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    authorized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revocation_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AutomaticSendClaim(Base):
    __tablename__ = "automatic_send_claims"
    __table_args__ = (
        CheckConstraint(
            "status IN ('claimed', 'sent', 'uncertain', 'released')",
            name="ck_automatic_send_claims_status",
        ),
        UniqueConstraint(
            "authorization_id", name="uq_automatic_send_claims_authorization"
        ),
        Index("ix_automatic_send_claims_owner_day", "owner_id", "claim_date"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    authorization_id: Mapped[UUID] = mapped_column(
        ForeignKey("automatic_send_authorizations.id", ondelete="RESTRICT")
    )
    draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("email_drafts.id", ondelete="RESTRICT"), index=True
    )
    claim_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        String(20), default="claimed", server_default="claimed"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AutomaticSendRequest(Base):
    __tablename__ = "automatic_send_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('generating', 'dispatching', 'sent', 'uncertain', 'failed')",
            name="ck_automatic_send_requests_status",
        ),
        UniqueConstraint(
            "owner_id", "idempotency_key", name="uq_automatic_send_requests_owner_key"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    idempotency_key: Mapped[UUID]
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(20), default="generating", server_default="generating"
    )
    draft_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("email_drafts.id", ondelete="RESTRICT"), nullable=True
    )
    authorization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("automatic_send_authorizations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    send_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("email_sends.id", ondelete="RESTRICT"), nullable=True
    )
    failure_category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

