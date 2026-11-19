from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.ai_provider import (
    AIEmailProvider,
    AIMalformedResponseError,
    AIRateLimitError,
    AITimeoutError,
    AIUnavailableError,
    EmailGenerationContext,
    get_ai_provider,
)
from app.database import get_db
from app.models import EmailDraft, EmailPurpose, EmailSend, GmailConnection, Resume, SenderProfile
from app.resume_text import ResumeExtractionError, extract_resume_text
from app.schemas import (
    ConversationDetail,
    EmailDraftResponse,
    FollowupGenerationRequest,
    HistoryConversationItem,
    HistoryMessage,
)
from app.settings_api import _commit, _owner_id, _storage_path


router = APIRouter(tags=["history"])


def _reply_subject(subject: str) -> str:
    base = subject.strip()
    while base.lower().startswith("re:"):
        base = base[3:].strip()
    return f"Re: {base}"


def _timestamp(send: EmailSend) -> datetime:
    return send.sent_at or send.approved_at or send.updated_at or send.created_at


def _root(conversation_id: UUID, db: Session) -> EmailSend:
    root = db.scalar(
        select(EmailSend).where(
            EmailSend.id == conversation_id,
            EmailSend.owner_id == _owner_id(),
            EmailSend.conversation_id.is_(None),
            EmailSend.status.in_(("sent", "uncertain")),
        )
    )
    if root is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return root


def _followups(root: EmailSend, db: Session) -> list[EmailSend]:
    return list(
        db.scalars(
            select(EmailSend)
            .where(
                EmailSend.owner_id == _owner_id(),
                EmailSend.conversation_id == root.id,
                EmailSend.status.in_(("sent", "uncertain")),
            )
            .order_by(func.coalesce(EmailSend.sent_at, EmailSend.updated_at).asc())
        )
    )


def _can_follow_up(
    root: EmailSend, connection: GmailConnection | None, db: Session
) -> bool:
    active_followup = db.scalar(
        select(EmailSend.id).where(
            EmailSend.owner_id == _owner_id(),
            EmailSend.conversation_id == root.id,
            EmailSend.status.in_(("pending", "sending", "uncertain")),
        )
    )
    return bool(
        root.status == "sent"
        and root.gmail_thread_id
        and root.rfc_message_id
        and connection
        and connection.is_valid
        and connection.email == root.sender_email
        and active_followup is None
    )


@router.get("/api/history", response_model=list[HistoryConversationItem])
def list_history(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: Annotated[str | None, Query(max_length=200)] = None,
) -> list[HistoryConversationItem]:
    statement = (
        select(EmailSend, EmailDraft)
        .join(EmailDraft, EmailDraft.id == EmailSend.draft_id)
        .where(
            EmailSend.owner_id == _owner_id(),
            EmailSend.conversation_id.is_(None),
            EmailSend.status.in_(("sent", "uncertain")),
        )
    )
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                EmailSend.recipient_email.ilike(pattern),
                EmailSend.subject.ilike(pattern),
                EmailDraft.company.ilike(pattern),
            )
        )
    rows = db.execute(
        statement.order_by(func.coalesce(EmailSend.sent_at, EmailSend.updated_at).desc())
        .offset(offset)
        .limit(limit)
    ).all()
    connection = db.scalar(
        select(GmailConnection).where(GmailConnection.owner_id == _owner_id())
    )
    result = []
    for root, draft in rows:
        followups = _followups(root, db)
        result.append(
            HistoryConversationItem(
                id=root.id,
                draft_id=root.draft_id,
                recruiter_name=draft.recipient_name,
                recruiter_email=root.recipient_email,
                company=draft.company,
                subject=root.subject,
                timestamp=_timestamp(root),
                delivery_state=root.status,
                follow_up_count=sum(item.status == "sent" for item in followups),
                uncertain_follow_up_count=sum(item.status == "uncertain" for item in followups),
                can_follow_up=_can_follow_up(root, connection, db),
            )
        )
    return result


def _history_message(send: EmailSend, *, original: bool) -> HistoryMessage:
    return HistoryMessage(
        id=send.id,
        draft_id=send.draft_id,
        kind="original" if original else "follow_up",
        sender_name=send.sender_name,
        sender_email=send.sender_email,
        recipient_email=send.recipient_email,
        subject=send.subject,
        body=send.body,
        timestamp=_timestamp(send),
        delivery_state=send.status,
        attachment_filename=send.attachment_filename,
        attachment_size_bytes=send.attachment_size_bytes,
        gmail_message_id=send.gmail_message_id,
        gmail_thread_id=send.gmail_thread_id,
    )


@router.get("/api/history/{conversation_id}", response_model=ConversationDetail)
def conversation_detail(
    conversation_id: UUID, db: Annotated[Session, Depends(get_db)]
) -> ConversationDetail:
    root = _root(conversation_id, db)
    draft = db.get(EmailDraft, root.draft_id)
    connection = db.scalar(
        select(GmailConnection).where(GmailConnection.owner_id == _owner_id())
    )
    return ConversationDetail(
        id=root.id,
        recruiter_name=draft.recipient_name if draft else None,
        recruiter_email=root.recipient_email,
        company=draft.company if draft else None,
        subject=root.subject,
        can_follow_up=_can_follow_up(root, connection, db),
        messages=[
            _history_message(root, original=True),
            *[_history_message(item, original=False) for item in _followups(root, db)],
        ],
    )


@router.post("/api/followups/generate", response_model=EmailDraftResponse)
async def generate_followup(
    payload: FollowupGenerationRequest,
    db: Annotated[Session, Depends(get_db)],
    provider: Annotated[AIEmailProvider, Depends(get_ai_provider)],
) -> EmailDraft:
    root = _root(payload.conversation_id, db)
    connection = db.scalar(
        select(GmailConnection).where(GmailConnection.owner_id == _owner_id())
    )
    if not _can_follow_up(root, connection, db):
        raise HTTPException(status_code=409, detail="Reconnect the original Gmail account before following up.")
    original = db.scalar(
        select(EmailDraft).where(
            EmailDraft.id == root.draft_id, EmailDraft.owner_id == _owner_id()
        )
    )
    if original is None:
        raise HTTPException(status_code=409, detail="The original draft context is unavailable.")
    purpose = db.scalar(
        select(EmailPurpose).where(
            EmailPurpose.id == original.purpose_id,
            EmailPurpose.owner_id == _owner_id(),
        )
    )
    profile = db.scalar(
        select(SenderProfile).where(SenderProfile.owner_id == _owner_id())
    )
    if purpose is None or profile is None or not profile.name.strip():
        raise HTTPException(status_code=409, detail="The saved follow-up context is unavailable.")

    resume = None
    resume_text = None
    if original.resume_id is not None:
        resume = db.scalar(
            select(Resume).where(
                Resume.id == original.resume_id, Resume.owner_id == _owner_id()
            )
        )
        if resume is not None:
            try:
                resume_text = extract_resume_text(_storage_path(resume.storage_key))
            except ResumeExtractionError:
                resume = None
                resume_text = None

    sent_messages = [root, *[item for item in _followups(root, db) if item.status == "sent"]]
    conversation_history = tuple(
        {"subject": item.subject[:200], "body": item.body[:3000]}
        for item in sent_messages[-10:]
    )
    context = EmailGenerationContext(
        sender_name=profile.name,
        recipient_name=original.recipient_name,
        company=original.company,
        role=original.role,
        job_description=original.job_description,
        purpose_title=purpose.title,
        purpose_description=purpose.description,
        resume_text=resume_text,
        resume_selected=resume is not None,
        attach_resume=False,
        conversation_history=conversation_history,
        followup_guidance=payload.guidance,
        is_followup=True,
    )
    try:
        generated = await provider.generate_email(context)
    except AIRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except AITimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except AIUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AIMalformedResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    draft = EmailDraft(
        owner_id=_owner_id(),
        purpose_id=purpose.id,
        resume_id=resume.id if resume else None,
        conversation_id=root.id,
        recipient_name=original.recipient_name,
        recipient_email=root.recipient_email,
        company=original.company,
        role=original.role,
        job_description=original.job_description,
        attach_resume=False,
        subject=_reply_subject(root.subject),
        body=generated.body,
    )
    db.add(draft)
    _commit(db)
    db.refresh(draft)
    return draft
