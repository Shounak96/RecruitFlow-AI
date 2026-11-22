import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.draft_safety import draft_fingerprint
from app.config import get_settings
from app.models import (
    AutomaticSendAuthorization,
    AutomaticSendClaim,
    EmailDraft,
    EmailSend,
    GmailConnection,
    Resume,
    SendingPreference,
)


class AutomaticSendingError(Exception):
    pass


_STORAGE_KEY_PATTERN = re.compile(r"^[0-9a-f]{32}\.pdf$")


@dataclass(frozen=True)
class AttachmentBinding:
    resume_id: UUID
    filename: str
    size_bytes: int
    sha256: str
    content: bytes


def load_automatic_attachment(
    db: Session, owner_id: UUID, draft: EmailDraft
) -> AttachmentBinding | None:
    if not draft.attach_resume:
        return None
    if draft.resume_id is None:
        raise AutomaticSendingError("The selected attachment is unavailable.")
    resume = db.scalar(
        select(Resume).where(Resume.id == draft.resume_id, Resume.owner_id == owner_id)
    )
    if resume is None or not _STORAGE_KEY_PATTERN.fullmatch(resume.storage_key):
        raise AutomaticSendingError("The selected attachment is unavailable.")
    root = get_settings().resume_storage_path.expanduser().resolve()
    path = (root / resume.storage_key).resolve()
    if path.parent != root or not path.is_file():
        raise AutomaticSendingError("The selected attachment is unavailable.")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise AutomaticSendingError("The selected attachment is unavailable.") from exc
    if len(content) != resume.size_bytes:
        raise AutomaticSendingError("The selected attachment changed.")
    return AttachmentBinding(
        resume_id=resume.id,
        filename=resume.original_filename,
        size_bytes=resume.size_bytes,
        sha256=hashlib.sha256(content).hexdigest(),
        content=content,
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_sending_preference(db: Session, owner_id: UUID) -> SendingPreference:
    preference = db.scalar(
        select(SendingPreference).where(SendingPreference.owner_id == owner_id)
    )
    if preference is None:
        preference = SendingPreference(owner_id=owner_id)
        db.add(preference)
        db.flush()
    return preference


def revoke_owner_authorizations(
    db: Session, owner_id: UUID, *, reason: str, now: datetime | None = None
) -> None:
    timestamp = now or utcnow()
    db.execute(
        update(AutomaticSendAuthorization)
        .where(
            AutomaticSendAuthorization.owner_id == owner_id,
            AutomaticSendAuthorization.revoked_at.is_(None),
        )
        .values(revoked_at=timestamp, revocation_reason=reason)
    )


def revoke_draft_authorizations(
    db: Session, owner_id: UUID, draft_id: UUID, *, reason: str
) -> None:
    db.execute(
        update(AutomaticSendAuthorization)
        .where(
            AutomaticSendAuthorization.owner_id == owner_id,
            AutomaticSendAuthorization.draft_id == draft_id,
            AutomaticSendAuthorization.revoked_at.is_(None),
        )
        .values(revoked_at=utcnow(), revocation_reason=reason)
    )


def validate_automatic_authorization(
    db: Session, owner_id: UUID, authorization_id: UUID
) -> tuple[AutomaticSendAuthorization, EmailDraft, SendingPreference]:
    authorization = db.scalar(
        select(AutomaticSendAuthorization).where(
            AutomaticSendAuthorization.id == authorization_id,
            AutomaticSendAuthorization.owner_id == owner_id,
        )
    )
    if authorization is None or authorization.revoked_at is not None:
        raise AutomaticSendingError("Automatic-send authorization is unavailable.")
    preference = db.scalar(
        select(SendingPreference).where(SendingPreference.owner_id == owner_id)
    )
    if (
        preference is None
        or preference.sending_mode != "automatic"
        or preference.automation_consented_at is None
    ):
        raise AutomaticSendingError("Automatic sending is not enabled by persisted consent.")
    draft = db.scalar(
        select(EmailDraft).where(
            EmailDraft.id == authorization.draft_id,
            EmailDraft.owner_id == owner_id,
            EmailDraft.deleted_at.is_(None),
        )
    )
    if draft is None or draft.sent_at is not None:
        raise AutomaticSendingError("The authorized draft is deleted or already sent.")
    if draft.reply_to_received_message_id is not None:
        raise AutomaticSendingError("Automatic replies to received messages are not allowed.")
    expected_kind = "follow_up" if draft.conversation_id is not None else "initial"
    if authorization.authorization_kind != expected_kind:
        raise AutomaticSendingError("The authorized draft type changed.")
    if expected_kind == "initial" and not preference.automatic_initial_outreach:
        raise AutomaticSendingError("Automatic initial outreach is disabled.")
    if expected_kind == "follow_up" and not preference.automatic_followups:
        raise AutomaticSendingError("Automatic follow-ups are disabled.")
    if (
        draft.recipient_email.lower() != authorization.recipient_email.lower()
        or draft_fingerprint(draft) != authorization.draft_fingerprint
    ):
        raise AutomaticSendingError("The authorized draft or recipient changed.")
    attachment = load_automatic_attachment(db, owner_id, draft)
    if attachment is None:
        if any(
            value is not None
            for value in (
                authorization.attachment_resume_id,
                authorization.attachment_filename,
                authorization.attachment_size_bytes,
                authorization.attachment_sha256,
            )
        ):
            raise AutomaticSendingError("The authorized attachment changed.")
    elif (
        authorization.attachment_resume_id != attachment.resume_id
        or authorization.attachment_filename != attachment.filename
        or authorization.attachment_size_bytes != attachment.size_bytes
        or authorization.attachment_sha256 != attachment.sha256
    ):
        raise AutomaticSendingError("The authorized attachment changed.")
    connection = db.scalar(
        select(GmailConnection).where(GmailConnection.owner_id == owner_id)
    )
    if (
        connection is None
        or not connection.is_valid
        or connection.email.lower() != authorization.sender_email.lower()
        or connection.google_subject != authorization.gmail_subject
    ):
        raise AutomaticSendingError("The authorized Gmail identity changed.")
    active_send = db.scalar(
        select(EmailSend.id).where(
            EmailSend.owner_id == owner_id,
            EmailSend.draft_id == draft.id,
            EmailSend.status.in_(("pending", "sending", "sent", "uncertain")),
        )
    )
    if active_send is not None:
        raise AutomaticSendingError("This draft already has a send attempt.")
    if draft.conversation_id is not None:
        root = db.scalar(
            select(EmailSend).where(
                EmailSend.id == draft.conversation_id,
                EmailSend.owner_id == owner_id,
                EmailSend.conversation_id.is_(None),
                EmailSend.status == "sent",
                EmailSend.deleted_at.is_(None),
            )
        )
        if (
            root is None
            or root.sender_email.lower() != connection.email.lower()
            or root.recipient_email.lower() != draft.recipient_email.lower()
        ):
            raise AutomaticSendingError("The authorized conversation changed.")
    return authorization, draft, preference


def authorize_automatic_draft(
    db: Session,
    owner_id: UUID,
    draft_id: UUID,
    recipient_email: str,
) -> AutomaticSendAuthorization:
    preference = db.scalar(
        select(SendingPreference)
        .where(SendingPreference.owner_id == owner_id)
        .with_for_update()
    )
    if (
        preference is None
        or preference.sending_mode != "automatic"
        or preference.automation_consented_at is None
    ):
        raise AutomaticSendingError("Explicit automatic-sending consent is required.")
    draft = db.scalar(
        select(EmailDraft)
        .where(
            EmailDraft.id == draft_id,
            EmailDraft.owner_id == owner_id,
            EmailDraft.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if draft is None:
        raise AutomaticSendingError("Draft not found or deleted.")
    if draft.sent_at is not None:
        raise AutomaticSendingError("A sent draft cannot be authorized.")
    if draft.reply_to_received_message_id is not None:
        raise AutomaticSendingError("Automatic replies to received messages are not allowed.")
    if draft.recipient_email.lower() != recipient_email.lower():
        raise AutomaticSendingError("The approved recipient does not match the draft.")
    kind = "follow_up" if draft.conversation_id is not None else "initial"
    if kind == "initial" and not preference.automatic_initial_outreach:
        raise AutomaticSendingError("Automatic initial outreach is disabled.")
    if kind == "follow_up" and not preference.automatic_followups:
        raise AutomaticSendingError("Automatic follow-ups are disabled.")
    connection = db.scalar(
        select(GmailConnection).where(
            GmailConnection.owner_id == owner_id,
            GmailConnection.is_valid.is_(True),
        )
    )
    if connection is None:
        raise AutomaticSendingError("A verified Gmail connection is required.")
    if kind == "follow_up":
        root = db.scalar(
            select(EmailSend).where(
                EmailSend.id == draft.conversation_id,
                EmailSend.owner_id == owner_id,
                EmailSend.conversation_id.is_(None),
                EmailSend.status == "sent",
                EmailSend.deleted_at.is_(None),
            )
        )
        if (
            root is None
            or root.sender_email.lower() != connection.email.lower()
            or root.recipient_email.lower() != draft.recipient_email.lower()
        ):
            raise AutomaticSendingError("The follow-up conversation is not eligible.")
    active_send = db.scalar(
        select(EmailSend.id).where(
            EmailSend.owner_id == owner_id,
            EmailSend.draft_id == draft.id,
            EmailSend.status.in_(("pending", "sending", "sent", "uncertain")),
        )
    )
    if active_send is not None:
        raise AutomaticSendingError("This draft already has a send attempt.")
    existing = db.scalar(
        select(AutomaticSendAuthorization.id).where(
            AutomaticSendAuthorization.owner_id == owner_id,
            AutomaticSendAuthorization.draft_id == draft.id,
            AutomaticSendAuthorization.revoked_at.is_(None),
        )
    )
    if existing is not None:
        raise AutomaticSendingError("This draft is already authorized.")
    attachment = load_automatic_attachment(db, owner_id, draft)
    authorization = AutomaticSendAuthorization(
        owner_id=owner_id,
        draft_id=draft.id,
        authorization_kind=kind,
        recipient_email=draft.recipient_email.lower(),
        sender_email=connection.email.lower(),
        gmail_subject=connection.google_subject,
        draft_fingerprint=draft_fingerprint(draft),
        attachment_resume_id=attachment.resume_id if attachment else None,
        attachment_filename=attachment.filename if attachment else None,
        attachment_size_bytes=attachment.size_bytes if attachment else None,
        attachment_sha256=attachment.sha256 if attachment else None,
    )
    db.add(authorization)
    db.flush()
    return authorization


def claim_automatic_send(
    db: Session,
    owner_id: UUID,
    authorization_id: UUID,
    *,
    now: datetime | None = None,
) -> AutomaticSendClaim:
    timestamp = now or utcnow()
    if timestamp.tzinfo is None:
        raise AutomaticSendingError("Automatic-send claim time must include a time zone.")
    timestamp = timestamp.astimezone(timezone.utc)
    preference = db.scalar(
        select(SendingPreference)
        .where(SendingPreference.owner_id == owner_id)
        .with_for_update()
    )
    if preference is None:
        raise AutomaticSendingError("Automatic sending is not enabled by persisted consent.")
    authorization, draft, validated_preference = validate_automatic_authorization(
        db, owner_id, authorization_id
    )
    if validated_preference.id != preference.id:
        raise AutomaticSendingError("Automatic sending preferences changed.")
    existing = db.scalar(
        select(AutomaticSendClaim.id).where(
            AutomaticSendClaim.authorization_id == authorization.id
        )
    )
    if existing is not None:
        raise AutomaticSendingError("This authorization already has a send claim.")
    used = int(
        db.scalar(
            select(func.count(AutomaticSendClaim.id)).where(
                AutomaticSendClaim.owner_id == owner_id,
                AutomaticSendClaim.claim_date == timestamp.date(),
                AutomaticSendClaim.status.in_(("claimed", "sent", "uncertain")),
            )
        )
        or 0
    )
    if used >= preference.daily_send_limit:
        raise AutomaticSendingError("The UTC automatic-send daily limit is reached.")
    claim = AutomaticSendClaim(
        owner_id=owner_id,
        authorization_id=authorization.id,
        draft_id=draft.id,
        claim_date=timestamp.date(),
        status="claimed",
    )
    db.add(claim)
    try:
        db.flush()
    except IntegrityError as exc:
        raise AutomaticSendingError(
            "This authorization already has a concurrent send claim."
        ) from exc
    return claim


def automatic_send_allowance(
    db: Session, owner_id: UUID, *, now: datetime | None = None
) -> tuple[int, int]:
    timestamp = (now or utcnow()).astimezone(timezone.utc)
    preference = ensure_sending_preference(db, owner_id)
    used = int(
        db.scalar(
            select(func.count(AutomaticSendClaim.id)).where(
                AutomaticSendClaim.owner_id == owner_id,
                AutomaticSendClaim.claim_date == timestamp.date(),
                AutomaticSendClaim.status.in_(("claimed", "sent", "uncertain")),
            )
        )
        or 0
    )
    return max(0, preference.daily_send_limit - used), preference.daily_send_limit


def validate_automatic_initial_preflight(
    db: Session, owner_id: UUID
) -> GmailConnection:
    preference = db.scalar(
        select(SendingPreference).where(SendingPreference.owner_id == owner_id)
    )
    if (
        preference is None
        or preference.sending_mode != "automatic"
        or preference.automation_consented_at is None
        or not preference.automatic_initial_outreach
    ):
        raise AutomaticSendingError(
            "Enable and explicitly opt in to automatic initial outreach first."
        )
    connection = db.scalar(
        select(GmailConnection).where(
            GmailConnection.owner_id == owner_id,
            GmailConnection.is_valid.is_(True),
        )
    )
    if connection is None:
        raise AutomaticSendingError("A verified Gmail connection is required.")
    remaining, _ = automatic_send_allowance(db, owner_id)
    if remaining < 1:
        raise AutomaticSendingError("The UTC automatic-send daily limit is reached.")
    return connection
