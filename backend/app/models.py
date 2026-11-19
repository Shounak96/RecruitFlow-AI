from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SenderProfile(Base):
    __tablename__ = "sender_profiles"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(unique=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    gmail_identity_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)


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

