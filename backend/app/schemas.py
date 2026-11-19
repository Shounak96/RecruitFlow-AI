from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SenderProfileResponse(BaseModel):
    name: str
    gmail_connected: bool
    gmail_email: str | None = None


class SenderProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be blank")
        return value


class ResumeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_name: str
    original_filename: str
    size_bytes: int
    created_at: datetime
    is_default: bool


class EmailPurposeBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=5000)

    @field_validator("title", "description")
    @classmethod
    def clean_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be blank")
        return value


class EmailPurposeCreate(EmailPurposeBase):
    pass


class EmailPurposeUpdate(EmailPurposeBase):
    pass


class EmailPurposeResponse(EmailPurposeBase):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    is_default: bool
    created_at: datetime
    updated_at: datetime


class DraftContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipient_name: str = Field(min_length=1, max_length=160)
    recipient_email: str = Field(min_length=3, max_length=320)
    company: str | None = Field(default=None, max_length=160)
    role: str | None = Field(default=None, max_length=160)
    job_description: str | None = Field(default=None, max_length=12_000)
    purpose_id: UUID
    resume_id: UUID | None = None
    attach_resume: bool = False

    @field_validator("recipient_name", "recipient_email")
    @classmethod
    def clean_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be blank")
        return value

    @field_validator("company", "role", "job_description")
    @classmethod
    def clean_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("recipient_email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        local, separator, domain = value.rpartition("@")
        if not separator or not local or "." not in domain or any(char.isspace() for char in value):
            raise ValueError("Enter a valid recipient email address")
        return value

    @model_validator(mode="after")
    def attachment_requires_resume(self):
        if self.attach_resume and self.resume_id is None:
            raise ValueError("A resume must be selected before it can be attached")
        return self


class EmailGenerationRequest(DraftContext):
    draft_id: UUID | None = None


class EmailDraftUpdate(DraftContext):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10_000)

    @field_validator("subject", "body")
    @classmethod
    def clean_draft_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Draft content cannot be blank")
        return value

    @field_validator("subject")
    @classmethod
    def subject_must_be_one_line(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("Subject must be one line")
        return value


class EmailDraftResponse(EmailDraftUpdate):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    created_at: datetime
    updated_at: datetime
    status: str = "not_sent"
    refinement_allowed: bool = True
    conversation_id: UUID | None = None
    reply_to_received_message_id: UUID | None = None
    draft_kind: Literal["initial", "follow_up", "reply"] = "initial"


class EmailRefinementRequest(EmailDraftUpdate):
    instruction: str = Field(min_length=1, max_length=1000)

    @field_validator("instruction")
    @classmethod
    def clean_instruction(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Refinement instruction cannot be blank")
        return value


class EmailRevisionResponse(BaseModel):
    subject: str
    body: str


class GmailStatusResponse(BaseModel):
    connected: bool
    email: str | None = None
    read_authorized: bool = False
    reauthorization_required: bool = False


class GmailAuthorizationResponse(BaseModel):
    authorization_url: str


class ReviewAttachmentResponse(BaseModel):
    filename: str
    size_bytes: int


class GmailReviewResponse(BaseModel):
    id: UUID
    draft_id: UUID
    status: Literal["pending", "sending", "sent", "uncertain", "failed", "stale"]
    sender_email: str
    recipient_email: str
    subject: str
    body: str
    attachment: ReviewAttachmentResponse | None
    created_at: datetime


class GmailSendApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: Literal[True]


class GmailSendResult(BaseModel):
    id: UUID
    draft_id: UUID
    status: Literal["sent", "uncertain", "failed"]
    gmail_message_id: str | None = None
    gmail_thread_id: str | None = None
    sent_at: datetime | None = None


class GmailSendHistoryResponse(BaseModel):
    id: UUID
    draft_id: UUID
    sender_email: str
    recipient_email: str
    subject: str
    attachment_filename: str | None
    attachment_size_bytes: int | None
    sent_at: datetime
    gmail_message_id: str
    gmail_thread_id: str


class FollowupGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    guidance: str | None = Field(default=None, max_length=2000)

    @field_validator("guidance")
    @classmethod
    def clean_guidance(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ReplyGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    instructions: str | None = Field(default=None, max_length=2000)

    @field_validator("instructions")
    @classmethod
    def clean_instructions(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class HistoryConversationItem(BaseModel):
    id: UUID
    draft_id: UUID
    recruiter_name: str | None
    recruiter_email: str
    company: str | None
    subject: str
    timestamp: datetime
    delivery_state: Literal["sent", "uncertain"]
    follow_up_count: int
    uncertain_follow_up_count: int
    can_follow_up: bool
    unread_reply_count: int


class HistoryMessage(BaseModel):
    id: UUID
    draft_id: UUID | None
    kind: Literal["original", "follow_up", "received"]
    direction: Literal["outgoing", "incoming"]
    sender_name: str | None
    sender_email: str
    recipient_email: str
    subject: str
    body: str
    full_body: str | None = None
    timestamp: datetime
    delivery_state: Literal["sent", "uncertain"] | None
    attachment_filename: str | None
    attachment_size_bytes: int | None
    gmail_message_id: str | None
    gmail_thread_id: str | None


class ConversationDetail(BaseModel):
    id: UUID
    recruiter_name: str | None
    recruiter_email: str
    company: str | None
    subject: str
    can_follow_up: bool
    unread_reply_count: int
    has_received_messages: bool
    can_reply: bool
    reply_eligibility: Literal[
        "eligible",
        "no_received_message",
        "draft_exists",
        "already_replied",
        "active_send",
        "gmail_unavailable",
        "unsupported_inbound",
    ]
    reply_draft_id: UUID | None = None
    messages: list[HistoryMessage]


class GmailReplySyncResponse(BaseModel):
    threads_checked: int
    threads_failed: int
    new_messages: int
    partial_failure: bool


