import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from email.utils import make_msgid
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.gmail_service import (
    GMAIL_READONLY_SCOPE,
    GMAIL_SCOPES,
    GmailConfigurationError,
    GmailCredentialError,
    GmailService,
    GmailUpstreamError,
    build_plain_text_message,
    get_gmail_service,
)
from app.models import (
    EmailDraft,
    EmailSend,
    GmailConnection,
    GmailOAuthState,
    ReceivedGmailMessage,
    Resume,
    SenderProfile,
)
from app.schemas import (
    GmailAuthorizationResponse,
    GmailReviewResponse,
    GmailSendApproval,
    GmailSendHistoryResponse,
    GmailSendResult,
    GmailStatusResponse,
    ReviewAttachmentResponse,
)
from app.settings_api import _commit, _ensure_profile, _owner_id, _storage_path


router = APIRouter(prefix="/api/gmail", tags=["gmail"])
STATE_LIFETIME = timedelta(minutes=10)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _state_hash(state_value: str) -> str:
    return hashlib.sha256(state_value.encode("utf-8")).hexdigest()


def _frontend_settings_url(result: str) -> str:
    origin = str(get_settings().frontend_origin).rstrip("/")
    return f"{origin}/settings?{urlencode({'gmail': result})}"


def _connection(db: Session, *, require_valid: bool = False) -> GmailConnection | None:
    connection = db.scalar(
        select(GmailConnection).where(GmailConnection.owner_id == _owner_id())
    )
    if require_valid and (connection is None or not connection.is_valid):
        raise HTTPException(status_code=409, detail="Connect Gmail before sending.")
    return connection


def _draft_fingerprint(draft: EmailDraft) -> str:
    canonical = json.dumps(
        {
            "draft_id": str(draft.id),
            "recipient_email": draft.recipient_email,
            "subject": draft.subject,
            "body": draft.body,
            "attach_resume": draft.attach_resume,
            "resume_id": str(draft.resume_id) if draft.resume_id else None,
            "conversation_id": str(draft.conversation_id) if draft.conversation_id else None,
            "reply_to_received_message_id": (
                str(draft.reply_to_received_message_id)
                if draft.reply_to_received_message_id
                else None
            ),
            "reply_context_fingerprint": draft.reply_context_fingerprint,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _review_response(send: EmailSend) -> GmailReviewResponse:
    attachment = None
    if send.attachment_filename is not None and send.attachment_size_bytes is not None:
        attachment = ReviewAttachmentResponse(
            filename=send.attachment_filename,
            size_bytes=send.attachment_size_bytes,
        )
    return GmailReviewResponse(
        id=send.id,
        draft_id=send.draft_id,
        status=send.status,
        sender_email=send.sender_email,
        recipient_email=send.recipient_email,
        subject=send.subject,
        body=send.body,
        attachment=attachment,
        created_at=send.created_at,
    )


def _reply_subject(subject: str) -> str:
    base = subject.strip()
    while base.lower().startswith("re:"):
        base = base[3:].strip()
    return f"Re: {base}"


def _conversation_messages(root: EmailSend, db: Session) -> list[EmailSend]:
    followups = list(
        db.scalars(
            select(EmailSend)
            .where(
                EmailSend.owner_id == _owner_id(),
                EmailSend.conversation_id == root.id,
                EmailSend.status == "sent",
            )
            .order_by(EmailSend.sent_at.asc())
        )
    )
    return [root, *followups]


def _received_fingerprint(message: ReceivedGmailMessage) -> str:
    canonical = json.dumps(
        {
            "id": str(message.id),
            "gmail_message_id": message.gmail_message_id,
            "gmail_thread_id": message.gmail_thread_id,
            "sender_email": message.sender_email,
            "recipient_email": message.recipient_email,
            "subject": message.subject,
            "body": message.body,
            "received_at": message.received_at.isoformat(),
            "rfc_message_id": message.rfc_message_id,
            "in_reply_to": message.in_reply_to,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _received_messages(root: EmailSend, db: Session) -> list[ReceivedGmailMessage]:
    return list(
        db.scalars(
            select(ReceivedGmailMessage)
            .where(
                ReceivedGmailMessage.owner_id == _owner_id(),
                ReceivedGmailMessage.conversation_id == root.id,
                ReceivedGmailMessage.gmail_thread_id == root.gmail_thread_id,
            )
            .order_by(
                ReceivedGmailMessage.received_at.asc(),
                ReceivedGmailMessage.created_at.asc(),
            )
        )
    )


async def _valid_access_token(
    connection: GmailConnection,
    db: Session,
    service: GmailService,
) -> str:
    if not connection.is_valid:
        raise HTTPException(status_code=409, detail="Reconnect Gmail before sending.")
    if connection.token_expires_at > _utcnow() + timedelta(seconds=60):
        try:
            return service.decrypt_token(connection.encrypted_access_token)
        except GmailCredentialError as exc:
            connection.is_valid = False
            _commit(db)
            raise HTTPException(status_code=409, detail="Reconnect Gmail before sending.") from exc
    try:
        refresh_token = service.decrypt_token(connection.encrypted_refresh_token)
        refreshed = await service.refresh_access_token(refresh_token)
    except GmailCredentialError as exc:
        connection.is_valid = False
        _commit(db)
        raise HTTPException(status_code=409, detail="The Gmail connection expired. Reconnect it.") from exc
    except GmailUpstreamError as exc:
        raise HTTPException(status_code=503, detail="Gmail authentication is unavailable.") from exc
    connection.encrypted_access_token = service.encrypt_token(refreshed.access_token)
    connection.token_expires_at = refreshed.expires_at
    if refreshed.refresh_token:
        connection.encrypted_refresh_token = service.encrypt_token(refreshed.refresh_token)
    _commit(db)
    return refreshed.access_token


@router.get("/status", response_model=GmailStatusResponse)
def gmail_status(db: Annotated[Session, Depends(get_db)]) -> GmailStatusResponse:
    connection = _connection(db)
    return GmailStatusResponse(
        connected=bool(connection and connection.is_valid),
        email=connection.email if connection else None,
        read_authorized=bool(
            connection
            and connection.is_valid
            and GMAIL_READONLY_SCOPE in connection.scopes.split()
        ),
        reauthorization_required=bool(
            connection
            and connection.is_valid
            and GMAIL_READONLY_SCOPE not in connection.scopes.split()
        ),
    )


@router.get("/oauth/authorize", response_model=GmailAuthorizationResponse)
def authorize_gmail(
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[GmailService, Depends(get_gmail_service)],
) -> GmailAuthorizationResponse:
    raw_state = secrets.token_urlsafe(32)
    now = _utcnow()
    db.execute(
        delete(GmailOAuthState).where(
            (GmailOAuthState.owner_id == _owner_id())
            | (GmailOAuthState.expires_at <= now)
        )
    )
    db.add(
        GmailOAuthState(
            owner_id=_owner_id(),
            state_hash=_state_hash(raw_state),
            expires_at=now + STATE_LIFETIME,
        )
    )
    try:
        authorization_url = service.authorization_url(raw_state)
    except GmailConfigurationError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    _commit(db)
    return GmailAuthorizationResponse(authorization_url=authorization_url)


@router.get("/oauth/callback")
async def gmail_oauth_callback(
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[GmailService, Depends(get_gmail_service)],
    state_value: Annotated[str, Query(alias="state", min_length=20, max_length=200)],
    code: Annotated[str | None, Query(max_length=4096)] = None,
    error: Annotated[str | None, Query(max_length=200)] = None,
) -> RedirectResponse:
    now = _utcnow()
    state_row = db.scalar(
        select(GmailOAuthState)
        .where(
            GmailOAuthState.owner_id == _owner_id(),
            GmailOAuthState.state_hash == _state_hash(state_value),
        )
        .with_for_update()
    )
    if state_row is None or state_row.expires_at <= now:
        if state_row is not None:
            db.delete(state_row)
            _commit(db)
        raise HTTPException(status_code=400, detail="OAuth state is invalid or expired.")
    db.delete(state_row)
    _commit(db)
    if error is not None:
        return RedirectResponse(_frontend_settings_url("denied"), status_code=303)
    if not code:
        raise HTTPException(status_code=400, detail="OAuth authorization code is missing.")

    try:
        grant = await service.complete_oauth(code)
    except GmailConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GmailCredentialError:
        return RedirectResponse(_frontend_settings_url("invalid"), status_code=303)
    except GmailUpstreamError:
        return RedirectResponse(_frontend_settings_url("unavailable"), status_code=303)

    connection = _connection(db)
    refresh_token = grant.refresh_token
    if (
        refresh_token is None
        and connection is not None
        and connection.google_subject == grant.google_subject
        and connection.email.lower() == grant.email.lower()
        and grant.scopes.issubset(set(connection.scopes.split()))
    ):
        try:
            refresh_token = service.decrypt_token(connection.encrypted_refresh_token)
        except GmailCredentialError:
            refresh_token = None
    if refresh_token is None:
        return RedirectResponse(_frontend_settings_url("missing_refresh"), status_code=303)

    if connection is None:
        connection = GmailConnection(owner_id=_owner_id())
        db.add(connection)
    connection.google_subject = grant.google_subject
    connection.email = grant.email
    connection.encrypted_access_token = service.encrypt_token(grant.access_token)
    connection.encrypted_refresh_token = service.encrypt_token(refresh_token)
    connection.token_expires_at = grant.expires_at
    connection.scopes = " ".join(sorted(grant.scopes))
    connection.is_valid = True
    profile = _ensure_profile(db)
    profile.gmail_identity_reference = grant.google_subject
    _commit(db)
    return RedirectResponse(_frontend_settings_url("connected"), status_code=303)


@router.delete("/connection", status_code=204)
async def disconnect_gmail(
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[GmailService, Depends(get_gmail_service)],
) -> Response:
    connection = _connection(db)
    if connection is None:
        return Response(status_code=204)
    try:
        token = service.decrypt_token(connection.encrypted_refresh_token)
        await service.revoke(token)
    except GmailCredentialError:
        pass
    except GmailUpstreamError as exc:
        raise HTTPException(
            status_code=503, detail="Google could not revoke the connection. Try again."
        ) from exc
    profile = _ensure_profile(db)
    profile.gmail_identity_reference = None
    db.delete(connection)
    _commit(db)
    return Response(status_code=204)


@router.post("/reviews/{draft_id}", response_model=GmailReviewResponse, status_code=201)
def create_review(
    draft_id: UUID,
    db: Annotated[Session, Depends(get_db)],
) -> GmailReviewResponse:
    connection = _connection(db, require_valid=True)
    draft = db.scalar(
        select(EmailDraft)
        .where(
            EmailDraft.id == draft_id,
            EmailDraft.owner_id == _owner_id(),
            EmailDraft.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found.")
    if draft.sent_at is not None:
        raise HTTPException(status_code=409, detail="This draft has already been sent.")
    active = db.scalar(
        select(EmailSend).where(
            EmailSend.draft_id == draft.id,
            EmailSend.owner_id == _owner_id(),
            EmailSend.status.in_(("sending", "sent", "uncertain")),
        )
    )
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail="This draft already has an active or completed send attempt.",
        )
    db.execute(
        update(EmailSend)
        .where(EmailSend.draft_id == draft.id, EmailSend.status == "pending")
        .values(status="stale")
    )

    attachment_resume_id = None
    attachment_filename = None
    attachment_size = None
    attachment_sha256 = None
    if draft.attach_resume:
        if draft.resume_id is None:
            raise HTTPException(status_code=409, detail="The selected attachment is unavailable.")
        resume = db.scalar(
            select(Resume).where(
                Resume.id == draft.resume_id, Resume.owner_id == _owner_id()
            )
        )
        if resume is None:
            raise HTTPException(status_code=409, detail="The selected attachment is unavailable.")
        attachment_path = _storage_path(resume.storage_key)
        try:
            attachment_bytes = attachment_path.read_bytes()
        except OSError as exc:
            raise HTTPException(
                status_code=409, detail="The selected attachment is unavailable."
            ) from exc
        if len(attachment_bytes) != resume.size_bytes:
            raise HTTPException(status_code=409, detail="The selected attachment changed.")
        attachment_resume_id = resume.id
        attachment_filename = resume.original_filename
        attachment_size = resume.size_bytes
        attachment_sha256 = hashlib.sha256(attachment_bytes).hexdigest()

    profile = _ensure_profile(db)
    conversation_id = None
    parent_send_id = None
    intended_thread_id = None
    in_reply_to = None
    references_header = None
    reply_to_received_message_id = None
    reply_context_fingerprint = None
    if draft.conversation_id is not None:
        root = db.scalar(
            select(EmailSend).where(
                EmailSend.id == draft.conversation_id,
                EmailSend.owner_id == _owner_id(),
                EmailSend.conversation_id.is_(None),
                EmailSend.deleted_at.is_(None),
            )
        )
        if (
            root is None
            or root.status != "sent"
            or not root.gmail_thread_id
            or root.sender_email != connection.email
        ):
            raise HTTPException(status_code=409, detail="The conversation is not eligible for follow-up.")
        messages = _conversation_messages(root, db)
        if any(not message.rfc_message_id for message in messages):
            raise HTTPException(
                status_code=409,
                detail="This older conversation lacks verified threading headers.",
            )
        parent = messages[-1]
        if draft.subject != _reply_subject(root.subject):
            raise HTTPException(status_code=409, detail="The follow-up subject must match its conversation.")
        received = None
        if draft.reply_to_received_message_id is not None:
            received_messages = _received_messages(root, db)
            received = next(
                (
                    item
                    for item in received_messages
                    if item.id == draft.reply_to_received_message_id
                ),
                None,
            )
            if (
                received is None
                or received_messages[-1].id != received.id
                or received.sender_email.lower() != root.recipient_email.lower()
                or (received.recipient_email or "").lower() != connection.email.lower()
                or draft.recipient_email.lower() != received.sender_email.lower()
                or draft.reply_context_fingerprint != _received_fingerprint(received)
                or not received.rfc_message_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="The received-message context changed. Generate a new AI reply.",
                )
            reply_to_received_message_id = received.id
            reply_context_fingerprint = _received_fingerprint(received)
        elif draft.recipient_email.lower() != root.recipient_email.lower():
            raise HTTPException(status_code=409, detail="The follow-up recipient changed.")
        conversation_id = root.id
        parent_send_id = parent.id
        intended_thread_id = root.gmail_thread_id
        in_reply_to = received.rfc_message_id if received is not None else parent.rfc_message_id
        reference_ids = [
            message.rfc_message_id for message in messages if message.rfc_message_id
        ]
        if received is not None:
            reference_ids.extend(
                item.rfc_message_id
                for item in received_messages
                if item.rfc_message_id
                and item.received_at <= received.received_at
            )
        references_header = " ".join(dict.fromkeys(reference_ids))

    sender_domain = connection.email.rpartition("@")[2] or None
    rfc_message_id = make_msgid(domain=sender_domain)

    send = EmailSend(
        owner_id=_owner_id(),
        draft_id=draft.id,
        conversation_id=conversation_id,
        parent_send_id=parent_send_id,
        reply_to_received_message_id=reply_to_received_message_id,
        reply_context_fingerprint=reply_context_fingerprint,
        status="pending",
        draft_fingerprint=_draft_fingerprint(draft),
        sender_email=connection.email,
        sender_name=profile.name,
        recipient_email=draft.recipient_email,
        subject=draft.subject,
        body=draft.body,
        attachment_resume_id=attachment_resume_id,
        attachment_filename=attachment_filename,
        attachment_size_bytes=attachment_size,
        attachment_sha256=attachment_sha256,
        rfc_message_id=rfc_message_id,
        intended_gmail_thread_id=intended_thread_id,
        in_reply_to=in_reply_to,
        references_header=references_header,
    )
    db.add(send)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="A send review already exists for this draft."
        ) from exc
    db.refresh(send)
    return _review_response(send)


@router.post("/sends/{send_id}", response_model=GmailSendResult)
async def approve_and_send(
    send_id: UUID,
    payload: GmailSendApproval,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[GmailService, Depends(get_gmail_service)],
) -> GmailSendResult:
    del payload  # Literal[True] validation is the explicit approval signal.
    connection = _connection(db, require_valid=True)
    access_token = await _valid_access_token(connection, db, service)

    send = db.scalar(
        select(EmailSend).where(
            EmailSend.id == send_id, EmailSend.owner_id == _owner_id()
        )
    )
    if send is None:
        raise HTTPException(status_code=404, detail="Send review not found.")
    draft = db.scalar(
        select(EmailDraft)
        .where(
            EmailDraft.id == send.draft_id,
            EmailDraft.owner_id == _owner_id(),
            EmailDraft.deleted_at.is_(None),
        )
        .with_for_update()
    )
    send = db.scalar(
        select(EmailSend)
        .where(EmailSend.id == send_id, EmailSend.owner_id == _owner_id())
        .with_for_update()
    )
    if send is None:
        raise HTTPException(status_code=404, detail="Send review not found.")
    if send.status != "pending":
        raise HTTPException(
            status_code=409, detail="This approval is no longer available for sending."
        )
    if send.conversation_id is not None:
        root = db.scalar(
            select(EmailSend).where(
                EmailSend.id == send.conversation_id,
                EmailSend.owner_id == _owner_id(),
                EmailSend.status == "sent",
                EmailSend.conversation_id.is_(None),
            )
        )
        if (
            root is None
            or root.sender_email != connection.email
            or root.gmail_thread_id != send.intended_gmail_thread_id
            or _conversation_messages(root, db)[-1].id != send.parent_send_id
        ):
            send.status = "stale"
            _commit(db)
            raise HTTPException(status_code=409, detail="The conversation changed after review.")
        if send.reply_to_received_message_id is not None:
            received_messages = _received_messages(root, db)
            received = next(
                (
                    item
                    for item in received_messages
                    if item.id == send.reply_to_received_message_id
                ),
                None,
            )
            if (
                received is None
                or not received_messages
                or received_messages[-1].id != received.id
                or draft is None
                or draft.reply_to_received_message_id != received.id
                or draft.reply_context_fingerprint != send.reply_context_fingerprint
                or send.recipient_email.lower() != received.sender_email.lower()
                or received.sender_email.lower() != root.recipient_email.lower()
                or (received.recipient_email or "").lower() != connection.email.lower()
                or send.reply_context_fingerprint != _received_fingerprint(received)
            ):
                send.status = "stale"
                _commit(db)
                raise HTTPException(
                    status_code=409,
                    detail="The received-message context changed after review.",
                )
    if draft is None or draft.sent_at is not None or _draft_fingerprint(draft) != send.draft_fingerprint:
        send.status = "stale"
        _commit(db)
        raise HTTPException(
            status_code=409, detail="The draft changed after review. Review it again."
        )

    attachment = None
    if send.attachment_resume_id is not None:
        resume = db.scalar(
            select(Resume).where(
                Resume.id == send.attachment_resume_id,
                Resume.owner_id == _owner_id(),
            )
        )
        if resume is None:
            send.status = "stale"
            _commit(db)
            raise HTTPException(status_code=409, detail="The reviewed attachment is unavailable.")
        path = _storage_path(resume.storage_key)
        if not path.is_file() or resume.original_filename != send.attachment_filename:
            send.status = "stale"
            _commit(db)
            raise HTTPException(status_code=409, detail="The reviewed attachment changed.")
        try:
            attachment_bytes = path.read_bytes()
        except OSError as exc:
            send.status = "failed"
            send.failure_category = "attachment_read"
            _commit(db)
            raise HTTPException(status_code=503, detail="The attachment could not be loaded.") from exc
        if (
            send.attachment_sha256 is None
            or len(attachment_bytes) != send.attachment_size_bytes
            or hashlib.sha256(attachment_bytes).hexdigest() != send.attachment_sha256
        ):
            send.status = "stale"
            _commit(db)
            raise HTTPException(status_code=409, detail="The reviewed attachment changed.")
        attachment = (send.attachment_filename or resume.original_filename, attachment_bytes)

    try:
        raw_message = build_plain_text_message(
            sender_email=send.sender_email,
            recipient_email=send.recipient_email,
            subject=send.subject,
            body=send.body,
            attachment=attachment,
            message_id=send.rfc_message_id,
            in_reply_to=send.in_reply_to,
            references=send.references_header,
        )
    except ValueError as exc:
        send.status = "failed"
        send.failure_category = "mime_validation"
        _commit(db)
        raise HTTPException(status_code=409, detail="The reviewed email is invalid.") from exc

    send.status = "sending"
    send.approved_at = _utcnow()
    _commit(db)
    try:
        if send.intended_gmail_thread_id is None:
            result = await service.send_message(access_token, raw_message)
        else:
            result = await service.send_message(
                access_token, raw_message, thread_id=send.intended_gmail_thread_id
            )
    except GmailCredentialError as exc:
        send.status = "failed"
        send.failure_category = "credentials"
        connection.is_valid = False
        _commit(db)
        raise HTTPException(status_code=409, detail="Reconnect Gmail before sending.") from exc
    except GmailUpstreamError as exc:
        send.status = "uncertain" if exc.uncertain else "failed"
        send.failure_category = exc.category
        _commit(db)
        if exc.uncertain:
            response.status_code = status.HTTP_202_ACCEPTED
            return GmailSendResult(
                id=send.id,
                draft_id=send.draft_id,
                status="uncertain",
            )
        raise HTTPException(status_code=502, detail="Gmail did not send the message.") from exc

    if (
        send.intended_gmail_thread_id is not None
        and result.thread_id != send.intended_gmail_thread_id
    ):
        send.status = "uncertain"
        send.failure_category = "thread_mismatch"
        send.gmail_message_id = result.message_id
        send.gmail_thread_id = result.thread_id
        _commit(db)
        response.status_code = status.HTTP_202_ACCEPTED
        return GmailSendResult(
            id=send.id,
            draft_id=send.draft_id,
            status="uncertain",
            gmail_message_id=result.message_id,
            gmail_thread_id=result.thread_id,
        )

    sent_at = _utcnow()
    send.status = "sent"
    send.sent_at = sent_at
    send.gmail_message_id = result.message_id
    send.gmail_thread_id = result.thread_id
    draft.sent_at = sent_at
    _commit(db)
    return GmailSendResult(
        id=send.id,
        draft_id=send.draft_id,
        status="sent",
        gmail_message_id=result.message_id,
        gmail_thread_id=result.thread_id,
        sent_at=sent_at,
    )


@router.get("/history", response_model=list[GmailSendHistoryResponse])
def send_history(
    db: Annotated[Session, Depends(get_db)],
) -> list[GmailSendHistoryResponse]:
    sends = db.scalars(
        select(EmailSend)
        .where(
            EmailSend.owner_id == _owner_id(),
            EmailSend.status == "sent",
            EmailSend.deleted_at.is_(None),
        )
        .order_by(EmailSend.sent_at.desc())
    )
    return [
        GmailSendHistoryResponse(
            id=item.id,
            draft_id=item.draft_id,
            sender_email=item.sender_email,
            recipient_email=item.recipient_email,
            subject=item.subject,
            attachment_filename=item.attachment_filename,
            attachment_size_bytes=item.attachment_size_bytes,
            sent_at=item.sent_at,
            gmail_message_id=item.gmail_message_id,
            gmail_thread_id=item.gmail_thread_id,
        )
        for item in sends
        if item.sent_at is not None
        and item.gmail_message_id is not None
        and item.gmail_thread_id is not None
    ]
