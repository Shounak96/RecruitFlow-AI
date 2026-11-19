from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
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
from app.drafts_api import _draft_response
from app.gmail_api import _received_fingerprint, _valid_access_token
from app.gmail_replies import clean_received_body, parse_gmail_message
from app.gmail_service import (
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    GmailCredentialError,
    GmailReadPermissionError,
    GmailService,
    GmailUpstreamError,
    get_gmail_service,
)
from app.models import (
    EmailDraft,
    EmailPurpose,
    EmailSend,
    GmailConnection,
    ReceivedGmailMessage,
    Resume,
    SenderProfile,
)
from app.resume_text import ResumeExtractionError, extract_resume_text
from app.schemas import (
    ConversationDetail,
    EmailDraftResponse,
    FollowupGenerationRequest,
    HistoryConversationItem,
    HistoryMessage,
    GmailReplySyncResponse,
    ReplyGenerationRequest,
)
from app.settings_api import _commit, _owner_id, _storage_path


router = APIRouter(tags=["history"])
MAX_SYNC_THREADS = 25
MAX_MESSAGES_PER_THREAD = 100


def _reply_subject(subject: str) -> str:
    base = subject.strip()
    while base.lower().startswith("re:"):
        base = base[3:].strip()
    return f"Re: {base}"


def _timestamp(send: EmailSend) -> datetime:
    return send.sent_at or send.approved_at or send.updated_at or send.created_at


def _root(
    conversation_id: UUID, db: Session, *, include_deleted: bool = False
) -> EmailSend:
    conditions = [
        EmailSend.id == conversation_id,
        EmailSend.owner_id == _owner_id(),
        EmailSend.conversation_id.is_(None),
        EmailSend.status.in_(("sent", "uncertain")),
    ]
    if not include_deleted:
        conditions.append(EmailSend.deleted_at.is_(None))
    root = db.scalar(select(EmailSend).where(*conditions))
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
                EmailSend.deleted_at.is_(None),
            )
            .order_by(func.coalesce(EmailSend.sent_at, EmailSend.updated_at).asc())
        )
    )


def _received(root: EmailSend, db: Session) -> list[ReceivedGmailMessage]:
    return list(
        db.scalars(
            select(ReceivedGmailMessage)
            .where(
                ReceivedGmailMessage.owner_id == _owner_id(),
                ReceivedGmailMessage.conversation_id == root.id,
            )
            .order_by(ReceivedGmailMessage.received_at.asc())
        )
    )


def _unread_count(root: EmailSend, db: Session) -> int:
    return int(
        db.scalar(
            select(func.count(ReceivedGmailMessage.id)).where(
                ReceivedGmailMessage.owner_id == _owner_id(),
                ReceivedGmailMessage.conversation_id == root.id,
                ReceivedGmailMessage.seen_at.is_(None),
            )
        )
        or 0
    )


def _latest_received(root: EmailSend, db: Session) -> ReceivedGmailMessage | None:
    return db.scalar(
        select(ReceivedGmailMessage)
        .where(
            ReceivedGmailMessage.owner_id == _owner_id(),
            ReceivedGmailMessage.conversation_id == root.id,
            ReceivedGmailMessage.gmail_thread_id == root.gmail_thread_id,
        )
        .order_by(
            ReceivedGmailMessage.received_at.desc(),
            ReceivedGmailMessage.created_at.desc(),
        )
        .limit(1)
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
        root.deleted_at is None
        and root.status == "sent"
        and root.gmail_thread_id
        and root.rfc_message_id
        and connection
        and connection.is_valid
        and connection.email == root.sender_email
        and active_followup is None
    )


def _reply_eligibility(
    root: EmailSend, connection: GmailConnection | None, db: Session
) -> tuple[str, UUID | None]:
    latest = _latest_received(root, db)
    if latest is None:
        return "no_received_message", None
    if (
        root.status != "sent"
        or not root.gmail_thread_id
        or not root.rfc_message_id
        or latest.gmail_thread_id != root.gmail_thread_id
        or latest.sender_email.lower() != root.recipient_email.lower()
        or (latest.recipient_email or "").lower() != root.sender_email.lower()
        or not latest.rfc_message_id
    ):
        return "unsupported_inbound", None
    completed_reply = db.scalar(
        select(EmailSend.id).where(
            EmailSend.owner_id == _owner_id(),
            EmailSend.reply_to_received_message_id == latest.id,
            EmailSend.status == "sent",
        )
    )
    if completed_reply is not None:
        return "already_replied", None
    active_send = db.scalar(
        select(EmailSend.id).where(
            EmailSend.owner_id == _owner_id(),
            EmailSend.conversation_id == root.id,
            EmailSend.status.in_(("pending", "sending", "uncertain")),
        )
    )
    if active_send is not None:
        return "active_send", None
    active_draft = db.scalar(
        select(EmailDraft.id).where(
            EmailDraft.owner_id == _owner_id(),
            EmailDraft.reply_to_received_message_id == latest.id,
            EmailDraft.deleted_at.is_(None),
        )
    )
    if active_draft is not None:
        return "draft_exists", active_draft
    if (
        connection is None
        or not connection.is_valid
        or connection.email.lower() != root.sender_email.lower()
        or GMAIL_SEND_SCOPE not in connection.scopes.split()
    ):
        return "gmail_unavailable", None
    return "eligible", None


def _can_reply(root: EmailSend, connection: GmailConnection | None, db: Session) -> bool:
    return _reply_eligibility(root, connection, db)[0] == "eligible"


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
            EmailSend.deleted_at.is_(None),
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
                unread_reply_count=_unread_count(root, db),
            )
        )
    return result


def _history_message(send: EmailSend, *, original: bool) -> HistoryMessage:
    return HistoryMessage(
        id=send.id,
        draft_id=send.draft_id,
        kind="original" if original else "follow_up",
        direction="outgoing",
        sender_name=send.sender_name,
        sender_email=send.sender_email,
        recipient_email=send.recipient_email,
        subject=send.subject,
        body=send.body,
        full_body=None,
        timestamp=_timestamp(send),
        delivery_state=send.status,
        attachment_filename=send.attachment_filename,
        attachment_size_bytes=send.attachment_size_bytes,
        gmail_message_id=send.gmail_message_id,
        gmail_thread_id=send.gmail_thread_id,
    )


def _received_history_message(message: ReceivedGmailMessage) -> HistoryMessage:
    cleaned_body = clean_received_body(message.body)
    return HistoryMessage(
        id=message.id,
        draft_id=None,
        kind="received",
        direction="incoming",
        sender_name=message.sender_name,
        sender_email=message.sender_email,
        recipient_email=message.recipient_email or "",
        subject=message.subject or "(No subject)",
        body=cleaned_body,
        full_body=message.body if cleaned_body != message.body else None,
        timestamp=message.received_at,
        delivery_state=None,
        attachment_filename=None,
        attachment_size_bytes=None,
        gmail_message_id=message.gmail_message_id,
        gmail_thread_id=message.gmail_thread_id,
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
    messages = [
        _history_message(root, original=True),
        *[_history_message(item, original=False) for item in _followups(root, db)],
        *[_received_history_message(item) for item in _received(root, db)],
    ]
    messages.sort(key=lambda item: item.timestamp)
    reply_eligibility, reply_draft_id = _reply_eligibility(root, connection, db)
    return ConversationDetail(
        id=root.id,
        recruiter_name=draft.recipient_name if draft else None,
        recruiter_email=root.recipient_email,
        company=draft.company if draft else None,
        subject=root.subject,
        can_follow_up=_can_follow_up(root, connection, db),
        unread_reply_count=_unread_count(root, db),
        has_received_messages=bool(_latest_received(root, db)),
        can_reply=reply_eligibility == "eligible",
        reply_eligibility=reply_eligibility,
        reply_draft_id=reply_draft_id,
        messages=messages,
    )


@router.post("/api/history/{conversation_id}/received/read", status_code=204)
def mark_received_messages_read(
    conversation_id: UUID, db: Annotated[Session, Depends(get_db)]
) -> Response:
    root = _root(conversation_id, db)
    db.execute(
        update(ReceivedGmailMessage)
        .where(
            ReceivedGmailMessage.owner_id == _owner_id(),
            ReceivedGmailMessage.conversation_id == root.id,
            ReceivedGmailMessage.seen_at.is_(None),
        )
        .values(seen_at=datetime.now(timezone.utc))
    )
    _commit(db)
    return Response(status_code=204)


@router.post("/api/gmail/replies/sync", response_model=GmailReplySyncResponse)
async def sync_gmail_replies(
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[GmailService, Depends(get_gmail_service)],
    limit: Annotated[int, Query(ge=1, le=MAX_SYNC_THREADS)] = MAX_SYNC_THREADS,
) -> GmailReplySyncResponse:
    connection = db.scalar(
        select(GmailConnection).where(GmailConnection.owner_id == _owner_id())
    )
    if connection is None or not connection.is_valid:
        raise HTTPException(status_code=409, detail="Connect Gmail before syncing replies.")
    if GMAIL_READONLY_SCOPE not in connection.scopes.split():
        raise HTTPException(
            status_code=409,
            detail="Reconnect Gmail to grant read-only access before syncing replies.",
        )
    access_token = await _valid_access_token(connection, db, service)
    roots = list(
        db.scalars(
            select(EmailSend)
            .where(
                EmailSend.owner_id == _owner_id(),
                EmailSend.conversation_id.is_(None),
                EmailSend.status == "sent",
                EmailSend.deleted_at.is_(None),
                EmailSend.gmail_thread_id.is_not(None),
                EmailSend.sender_email == connection.email,
            )
            .order_by(EmailSend.sent_at.desc())
            .limit(limit)
        )
    )
    checked = failed = created = 0
    for root in roots:
        checked += 1
        try:
            thread = await service.get_thread(access_token, root.gmail_thread_id or "")
        except GmailReadPermissionError as exc:
            connection.scopes = " ".join(
                scope for scope in connection.scopes.split() if scope != GMAIL_READONLY_SCOPE
            )
            _commit(db)
            raise HTTPException(
                status_code=409,
                detail="Reconnect Gmail to grant read-only access before syncing replies.",
            ) from exc
        except GmailCredentialError as exc:
            connection.is_valid = False
            _commit(db)
            raise HTTPException(
                status_code=409, detail="The Gmail connection expired. Reconnect it."
            ) from exc
        except GmailUpstreamError:
            failed += 1
            continue
        if thread.get("id") != root.gmail_thread_id or not isinstance(thread.get("messages"), list):
            failed += 1
            continue
        live_root = db.scalar(
            select(EmailSend)
            .where(
                EmailSend.id == root.id,
                EmailSend.owner_id == _owner_id(),
                EmailSend.deleted_at.is_(None),
                EmailSend.status == "sent",
            )
            .with_for_update()
        )
        if live_root is None:
            db.rollback()
            continue
        outgoing_ids = set(
            db.scalars(
                select(EmailSend.gmail_message_id).where(
                    EmailSend.owner_id == _owner_id(),
                    or_(EmailSend.id == live_root.id, EmailSend.conversation_id == live_root.id),
                    EmailSend.gmail_message_id.is_not(None),
                )
            )
        )
        for raw_message in thread["messages"][-MAX_MESSAGES_PER_THREAD:]:
            if not isinstance(raw_message, dict):
                continue
            parsed = parse_gmail_message(raw_message)
            if (
                parsed is None
                or parsed.gmail_thread_id != live_root.gmail_thread_id
                or parsed.gmail_message_id in outgoing_ids
                or parsed.sender_email == connection.email.lower()
                or parsed.sender_email != live_root.recipient_email.lower()
                or parsed.recipient_emails != frozenset({connection.email.lower()})
            ):
                continue
            statement = (
                insert(ReceivedGmailMessage)
                .values(
                    owner_id=_owner_id(),
                    conversation_id=live_root.id,
                    gmail_message_id=parsed.gmail_message_id,
                    gmail_thread_id=parsed.gmail_thread_id,
                    sender_name=parsed.sender_name,
                    sender_email=parsed.sender_email,
                    recipient_email=connection.email,
                    subject=parsed.subject,
                    body=parsed.body,
                    received_at=parsed.received_at,
                    rfc_message_id=parsed.rfc_message_id,
                    in_reply_to=parsed.in_reply_to,
                )
                .on_conflict_do_nothing(
                    constraint="uq_received_gmail_messages_owner_message"
                )
                .returning(ReceivedGmailMessage.id)
            )
            if db.execute(statement).scalar_one_or_none() is not None:
                created += 1
                db.execute(
                    update(EmailSend)
                    .where(
                        EmailSend.owner_id == _owner_id(),
                        EmailSend.conversation_id == live_root.id,
                        EmailSend.reply_to_received_message_id.is_not(None),
                        EmailSend.status == "pending",
                    )
                    .values(status="stale")
                )
        _commit(db)
    return GmailReplySyncResponse(
        threads_checked=checked,
        threads_failed=failed,
        new_messages=created,
        partial_failure=failed > 0,
    )


@router.post("/api/replies/generate", response_model=EmailDraftResponse)
async def generate_reply(
    payload: ReplyGenerationRequest,
    db: Annotated[Session, Depends(get_db)],
    provider: Annotated[AIEmailProvider, Depends(get_ai_provider)],
) -> EmailDraftResponse:
    root = _root(payload.conversation_id, db)
    connection = db.scalar(
        select(GmailConnection).where(GmailConnection.owner_id == _owner_id())
    )
    latest = _latest_received(root, db)
    if latest is None:
        raise HTTPException(status_code=409, detail="Sync a recruiter reply before generating an AI reply.")
    if not _can_reply(root, connection, db):
        raise HTTPException(
            status_code=409,
            detail="This received message is not eligible for an AI reply. Reopen the latest active conversation or reconnect Gmail.",
        )
    selected_context_fingerprint = _received_fingerprint(latest)
    original = db.scalar(
        select(EmailDraft).where(
            EmailDraft.id == root.draft_id,
            EmailDraft.owner_id == _owner_id(),
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
        raise HTTPException(status_code=409, detail="The saved reply context is unavailable.")

    resume = None
    resume_text = None
    if original.resume_id is not None:
        resume = db.scalar(
            select(Resume).where(
                Resume.id == original.resume_id,
                Resume.owner_id == _owner_id(),
            )
        )
        if resume is not None:
            try:
                resume_text = extract_resume_text(_storage_path(resume.storage_key))
            except ResumeExtractionError:
                resume = None
                resume_text = None

    sent_messages = [root, *[item for item in _followups(root, db) if item.status == "sent"]]
    combined = [
        (
            item.sent_at or item.created_at,
            {
                "direction": "outgoing",
                "subject": item.subject[:200],
                "body": item.body[:3000],
            },
        )
        for item in sent_messages
    ]
    combined.extend(
        (
            item.received_at,
            {
                "direction": "incoming",
                "subject": (item.subject or "")[:200],
                "body": clean_received_body(item.body)[:3000],
            },
        )
        for item in _received(root, db)
    )
    combined.sort(key=lambda item: item[0])
    context = EmailGenerationContext(
        sender_name=profile.name,
        recipient_name=latest.sender_name or latest.sender_email,
        company=original.company,
        role=original.role,
        job_description=original.job_description,
        purpose_title=purpose.title,
        purpose_description=purpose.description,
        resume_text=resume_text,
        resume_selected=resume is not None,
        attach_resume=False,
        conversation_history=tuple(item[1] for item in combined[-12:]),
        reply_guidance=payload.instructions,
        is_reply=True,
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

    locked_root = db.scalar(
        select(EmailSend)
        .where(
            EmailSend.id == root.id,
            EmailSend.owner_id == _owner_id(),
            EmailSend.status == "sent",
            EmailSend.deleted_at.is_(None),
        )
        .with_for_update()
    )
    latest_after_generation = _latest_received(root, db) if locked_root is not None else None
    if (
        locked_root is None
        or latest_after_generation is None
        or latest_after_generation.id != latest.id
        or _received_fingerprint(latest_after_generation) != selected_context_fingerprint
        or not _can_reply(locked_root, connection, db)
    ):
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A newer recruiter reply arrived. Generate a response from the latest conversation.",
        )
    draft = EmailDraft(
        owner_id=_owner_id(),
        purpose_id=purpose.id,
        resume_id=resume.id if resume else None,
        conversation_id=root.id,
        reply_to_received_message_id=latest.id,
        reply_context_fingerprint=selected_context_fingerprint,
        recipient_name=latest.sender_name or latest.sender_email,
        recipient_email=latest.sender_email,
        company=original.company,
        role=original.role,
        job_description=original.job_description,
        attach_resume=False,
        subject=_reply_subject(root.subject),
        body=generated.body,
    )
    db.add(draft)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A draft reply already exists for the latest received message.",
        ) from exc
    db.refresh(draft)
    return _draft_response(draft, db)


@router.delete("/api/history/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: UUID, db: Annotated[Session, Depends(get_db)]
) -> Response:
    candidate = _root(conversation_id, db, include_deleted=True)
    drafts = list(
        db.scalars(
            select(EmailDraft)
            .where(
                EmailDraft.owner_id == _owner_id(),
                or_(
                    EmailDraft.id == candidate.draft_id,
                    EmailDraft.conversation_id == candidate.id,
                ),
            )
            .with_for_update()
        )
    )
    sends = list(
        db.scalars(
            select(EmailSend)
            .where(
                EmailSend.owner_id == _owner_id(),
                or_(EmailSend.id == candidate.id, EmailSend.conversation_id == candidate.id),
            )
            .with_for_update()
        )
    )
    root = next((send for send in sends if send.id == candidate.id), None)
    if root is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    if root.deleted_at is not None:
        return Response(status_code=204)
    if any(send.status == "sending" for send in sends):
        raise HTTPException(
            status_code=409,
            detail="This conversation cannot be deleted while Gmail delivery is in progress.",
        )
    deleted_at = datetime.now(timezone.utc)
    for draft in drafts:
        draft.deleted_at = deleted_at
    for send in sends:
        if send.status == "pending":
            send.status = "stale"
        send.deleted_at = deleted_at
    _commit(db)
    return Response(status_code=204)


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

    root = _root(payload.conversation_id, db)
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
    return _draft_response(draft, db)
