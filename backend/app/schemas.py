from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _clean_url(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip()
    if len(value) > 500 or not value.lower().startswith(("http://", "https://")):
        raise ValueError("Links must be valid http:// or https:// URLs")
    return value


class ProfileEducationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    degree: str = Field(min_length=1, max_length=160)
    field: str | None = Field(default=None, max_length=160)
    institution: str = Field(min_length=1, max_length=200)
    graduation_status: Literal["completed", "in_progress", "unspecified"] = "unspecified"
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def dates_in_order(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("Education end date cannot be before start date")
        return self


class ProfileExperienceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=160)
    employment_type: Literal["full_time", "internship", "contract", "part_time", "other"] = "other"
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    work_summary: str = Field(min_length=1, max_length=5000)
    technologies: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def dates_in_order(self):
        if self.is_current and self.end_date:
            raise ValueError("Current experience cannot have an end date")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("Experience end date cannot be before start date")
        return self


class ProfileSkillGroupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(min_length=1, max_length=100)
    skills: list[str] = Field(min_length=1, max_length=50)


class ProfileProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    technologies: list[str] = Field(default_factory=list, max_length=50)
    description: str = Field(min_length=1, max_length=5000)


class ProfileCustomLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=80)
    url: str
    @field_validator("url")
    @classmethod
    def validate_url(cls, value): return _clean_url(value)


class ProfessionalProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str = Field(default="", max_length=120)
    headline: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    location: str | None = Field(default=None, max_length=160)
    summary: str | None = Field(default=None, max_length=2000)
    experience_duration: str | None = Field(default=None, max_length=120)
    github_url: str | None = None
    linkedin_url: str | None = None
    portfolio_url: str | None = None
    custom_links: list[ProfileCustomLink] = Field(default_factory=list, max_length=10)
    education: list[ProfileEducationInput] = Field(default_factory=list, max_length=20)
    experiences: list[ProfileExperienceInput] = Field(default_factory=list, max_length=30)
    skill_groups: list[ProfileSkillGroupInput] = Field(default_factory=list, max_length=20)
    projects: list[ProfileProjectInput] = Field(default_factory=list, max_length=5)

    @field_validator("github_url", "linkedin_url", "portfolio_url")
    @classmethod
    def validate_url(cls, value): return _clean_url(value)

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, value): return value.strip()


class ProfessionalProfileResponse(ProfessionalProfileUpdate):
    version: int
    updated_at: datetime
    verified_gmail_email: str | None = None


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


class SendingPreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sending_mode: Literal["manual", "automatic"]
    automatic_initial_outreach: bool = False
    automatic_followups: bool = False
    daily_send_limit: int = Field(default=5, ge=1, le=500, strict=True)
    confirm_automatic_sending: bool = False

    @model_validator(mode="after")
    def validate_mode(self):
        if self.sending_mode == "manual" and (
            self.automatic_initial_outreach or self.automatic_followups
        ):
            raise ValueError("Manual mode cannot enable automatic send categories")
        if self.sending_mode == "automatic" and not (
            self.automatic_initial_outreach or self.automatic_followups
        ):
            raise ValueError("Select at least one automatic send category")
        return self


class SendingPreferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sending_mode: Literal["manual", "automatic"]
    automatic_initial_outreach: bool
    automatic_followups: bool
    daily_send_limit: int = Field(ge=1, le=500)
    automation_consented_at: datetime | None
    automation_disabled_at: datetime | None
    stage_a_dispatch_enabled: Literal[False] = False


class AutomaticSendAuthorizationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: UUID
    recipient_email: str = Field(min_length=3, max_length=320)
    approved: Literal[True]

    @field_validator("recipient_email")
    @classmethod
    def clean_recipient(cls, value: str) -> str:
        value = value.strip().lower()
        local, separator, domain = value.rpartition("@")
        if not separator or not local or "." not in domain or any(char.isspace() for char in value):
            raise ValueError("Enter a valid recipient email address")
        return value


class AutomaticSendAuthorizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    draft_id: UUID
    authorization_kind: Literal["initial", "follow_up"]
    recipient_email: str
    sender_email: str
    attachment_filename: str | None
    attachment_size_bytes: int | None
    authorized_at: datetime
    revoked_at: datetime | None
    revocation_reason: str | None


class AutomaticSendAttachmentResponse(BaseModel):
    filename: str
    size_bytes: int


class AutomaticSendPreviewResponse(BaseModel):
    draft_id: UUID
    authorization_id: UUID | None = None
    authorization_status: Literal["eligible", "authorized", "sent", "uncertain"]
    sender_email: str
    recipient_email: str
    subject: str
    body: str
    attachment: AutomaticSendAttachmentResponse | None
    remaining_today: int
    daily_limit: int


class AutomaticSendDispatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_authorized_send: Literal[True]


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
    include_github: bool = False
    include_linkedin: bool = False
    include_portfolio: bool = False

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


class AutomaticEmailGenerationRequest(DraftContext):
    idempotency_key: UUID


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


class AutomaticEmailSendResult(GmailSendResult):
    idempotency_key: UUID


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


