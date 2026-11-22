import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select, update
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
    validate_generated_email,
)
from app.automatic_sending import (
    AutomaticSendingError,
    authorize_automatic_draft,
    revoke_draft_authorizations,
    validate_automatic_initial_preflight,
)
from app.database import get_db
from app.gmail_api import _received_fingerprint, run_authorized_send
from app.gmail_service import GmailService, get_gmail_service
from app.gmail_replies import clean_received_body
from app.models import (
    AutomaticSendRequest,
    EmailDraft,
    EmailPurpose,
    EmailSend,
    ReceivedGmailMessage,
    Resume,
    ProfessionalProfile,
    SenderProfile,
)
from app.profile_service import (
    append_signature_links, profile_snapshot, remove_signature_links,
    selected_links, snapshot_fingerprint,
)
from app.schemas import (
    AutomaticEmailGenerationRequest,
    AutomaticEmailSendResult,
    AutomaticSendDispatch,
    EmailDraftResponse,
    EmailDraftUpdate,
    EmailGenerationRequest,
    EmailRefinementRequest,
    EmailRevisionResponse,
)
from app.settings_api import _commit, _owner_id, _storage_path

router = APIRouter(prefix="/api/drafts", tags=["drafts"])


def _owned_purpose(purpose_id: UUID, db: Session) -> EmailPurpose:
    purpose = db.scalar(
        select(EmailPurpose).where(
            EmailPurpose.id == purpose_id, EmailPurpose.owner_id == _owner_id()
        )
    )
    if purpose is None:
        raise HTTPException(status_code=404, detail="Email purpose not found.")
    return purpose


def _owned_resume(resume_id: UUID | None, db: Session) -> Resume | None:
    if resume_id is None:
        return None
    resume = db.scalar(
        select(Resume).where(Resume.id == resume_id, Resume.owner_id == _owner_id())
    )
    if resume is None:
        raise HTTPException(status_code=404, detail="Resume not found.")
    return resume


def _professional_profile(db: Session) -> ProfessionalProfile:
    profile = db.scalar(
        select(ProfessionalProfile).where(ProfessionalProfile.owner_id == _owner_id())
    )
    if profile is None:
        legacy = db.scalar(select(SenderProfile).where(SenderProfile.owner_id == _owner_id()))
        if legacy is not None and legacy.name.strip():
            profile = ProfessionalProfile(owner_id=_owner_id(), full_name=legacy.name)
            db.add(profile); db.flush()
    if profile is None or not profile.full_name.strip():
        raise HTTPException(status_code=422, detail="Complete your professional Profile before generating email.")
    return profile


def _profile_generation_state(payload, db: Session) -> tuple[dict, str, dict[str, str]]:
    profile = _professional_profile(db)
    snapshot = profile_snapshot(db, _owner_id(), payload.job_description)
    links = selected_links(
        profile, github=payload.include_github,
        linkedin=payload.include_linkedin, portfolio=payload.include_portfolio,
    )
    if payload.include_github and "GitHub" not in links:
        raise HTTPException(status_code=422, detail="Add a GitHub URL to Profile before including it.")
    if payload.include_linkedin and "LinkedIn" not in links:
        raise HTTPException(status_code=422, detail="Add a LinkedIn URL to Profile before including it.")
    if payload.include_portfolio and "Portfolio" not in links:
        raise HTTPException(status_code=422, detail="Add a portfolio URL to Profile before including it.")
    return snapshot, snapshot_fingerprint(snapshot), links


def _owned_draft(
    draft_id: UUID,
    db: Session,
    *,
    for_update: bool = False,
    include_deleted: bool = False,
) -> EmailDraft:
    conditions = [EmailDraft.id == draft_id, EmailDraft.owner_id == _owner_id()]
    if not include_deleted:
        conditions.append(EmailDraft.deleted_at.is_(None))
    statement = select(EmailDraft).where(*conditions)
    if for_update:
        statement = statement.with_for_update()
    draft = db.scalar(statement)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return draft


def _ensure_unsent(draft: EmailDraft, db: Session) -> None:
    if draft.sent_at is not None:
        raise HTTPException(status_code=409, detail="Sent drafts are read-only.")
    active_send = db.scalar(
        select(EmailSend.id).where(
            EmailSend.draft_id == draft.id,
            EmailSend.owner_id == _owner_id(),
            EmailSend.status.in_(("sending", "sent", "uncertain")),
        )
    )
    if active_send is not None:
        raise HTTPException(
            status_code=409,
            detail="Drafts with an active or uncertain send attempt are read-only.",
        )


def _refinement_allowed(draft: EmailDraft, db: Session) -> bool:
    if draft.sent_at is not None:
        return False
    active_send = db.scalar(
        select(EmailSend.id).where(
            EmailSend.draft_id == draft.id,
            EmailSend.owner_id == _owner_id(),
            EmailSend.status.in_(("sending", "sent", "uncertain")),
        )
    )
    return active_send is None


def _draft_response(draft: EmailDraft, db: Session) -> EmailDraftResponse:
    response = EmailDraftResponse.model_validate(draft)
    kind = (
        "reply"
        if draft.reply_to_received_message_id is not None
        else "follow_up"
        if draft.conversation_id is not None
        else "initial"
    )
    links = draft.included_profile_links or {}
    return response.model_copy(update={
        "refinement_allowed": _refinement_allowed(draft, db), "draft_kind": kind,
        "include_github": "GitHub" in links, "include_linkedin": "LinkedIn" in links,
        "include_portfolio": "Portfolio" in links,
    })


def _reply_subject(subject: str) -> str:
    base = subject.strip()
    while base.lower().startswith("re:"):
        base = base[3:].strip()
    return f"Re: {base}"


def _followup_root(draft: EmailDraft, db: Session) -> EmailSend | None:
    if draft.conversation_id is None:
        return None
    root = db.scalar(
        select(EmailSend).where(
            EmailSend.id == draft.conversation_id,
            EmailSend.owner_id == _owner_id(),
            EmailSend.conversation_id.is_(None),
            EmailSend.status == "sent",
            EmailSend.deleted_at.is_(None),
        )
    )
    if root is None:
        raise HTTPException(status_code=409, detail="The follow-up conversation is unavailable.")
    return root


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


def _bound_received(draft: EmailDraft, root: EmailSend, db: Session) -> ReceivedGmailMessage | None:
    if draft.reply_to_received_message_id is None:
        return None
    message = db.scalar(
        select(ReceivedGmailMessage).where(
            ReceivedGmailMessage.id == draft.reply_to_received_message_id,
            ReceivedGmailMessage.owner_id == _owner_id(),
            ReceivedGmailMessage.conversation_id == root.id,
            ReceivedGmailMessage.gmail_thread_id == root.gmail_thread_id,
        )
    )
    latest = _latest_received(root, db)
    if (
        message is None
        or latest is None
        or message.id != latest.id
        or draft.reply_context_fingerprint != _received_fingerprint(message)
    ):
        raise HTTPException(
            status_code=409,
            detail="A newer or unavailable recruiter reply makes this draft stale. Generate a new AI reply.",
        )
    if not message.sender_email or message.sender_email.lower() != root.recipient_email.lower():
        raise HTTPException(status_code=409, detail="The received-message sender is not permitted.")
    return message


def _validate_followup_payload(
    draft: EmailDraft, payload: EmailDraftUpdate | EmailRefinementRequest, db: Session
) -> EmailSend | None:
    root = _followup_root(draft, db)
    if root is None:
        return None
    received = _bound_received(draft, root, db)
    expected_recipient = received.sender_email if received is not None else root.recipient_email
    if payload.recipient_email.lower() != expected_recipient.lower():
        raise HTTPException(status_code=409, detail="A follow-up recipient cannot be changed.")
    if payload.subject != _reply_subject(root.subject):
        raise HTTPException(status_code=409, detail="A follow-up subject cannot leave its thread.")
    return root


def _invalidate_pending_reviews(draft_id: UUID, db: Session) -> None:
    db.execute(
        update(EmailSend)
        .where(EmailSend.draft_id == draft_id, EmailSend.status == "pending")
        .values(status="stale")
    )


def _apply_context(draft: EmailDraft, payload: EmailGenerationRequest | EmailDraftUpdate) -> None:
    draft.purpose_id = payload.purpose_id
    draft.resume_id = payload.resume_id
    draft.recipient_name = payload.recipient_name
    draft.recipient_email = payload.recipient_email
    draft.company = payload.company
    draft.role = payload.role
    draft.job_description = payload.job_description
    draft.attach_resume = payload.attach_resume


def _generation_context(
    *,
    payload: EmailGenerationRequest | EmailRefinementRequest,
    sender_name: str,
    purpose: EmailPurpose,
    profile_context: dict,
    current_subject: str | None = None,
    current_body: str | None = None,
    refinement_instruction: str | None = None,
    conversation_history: tuple[dict[str, str], ...] = (),
    is_followup: bool = False,
    reply_guidance: str | None = None,
    is_reply: bool = False,
) -> EmailGenerationContext:
    return EmailGenerationContext(
        sender_name=sender_name,
        recipient_name=payload.recipient_name,
        company=payload.company,
        role=payload.role,
        job_description=payload.job_description,
        purpose_title=purpose.title,
        purpose_description=purpose.description,
        resume_selected=payload.resume_id is not None,
        attach_resume=payload.attach_resume,
        profile_context=profile_context,
        current_subject=current_subject,
        current_body=current_body,
        refinement_instruction=refinement_instruction,
        conversation_history=conversation_history,
        is_followup=is_followup,
        reply_guidance=reply_guidance,
        is_reply=is_reply,
    )


def _automatic_request_fingerprint(payload: AutomaticEmailGenerationRequest) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json", exclude={"idempotency_key"}),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _stored_automatic_result(
    request_row: AutomaticSendRequest, db: Session
) -> AutomaticEmailSendResult | None:
    send = db.get(EmailSend, request_row.send_id) if request_row.send_id else None
    if send is None and request_row.draft_id is not None:
        send = db.scalar(
            select(EmailSend)
            .where(
                EmailSend.owner_id == request_row.owner_id,
                EmailSend.draft_id == request_row.draft_id,
            )
            .order_by(EmailSend.created_at.desc())
        )
    if send is None or send.status not in {"sent", "uncertain"}:
        return None
    request_row.send_id = send.id
    request_row.status = send.status
    _commit(db)
    return AutomaticEmailSendResult(
        id=send.id,
        draft_id=send.draft_id,
        status=send.status,
        gmail_message_id=send.gmail_message_id,
        gmail_thread_id=send.gmail_thread_id,
        sent_at=send.sent_at,
        idempotency_key=request_row.idempotency_key,
    )


def _fail_automatic_request(
    db: Session, request_id: UUID, *, category: str
) -> None:
    db.rollback()
    request_row = db.get(AutomaticSendRequest, request_id)
    if request_row is not None and request_row.status not in {"sent", "uncertain"}:
        request_row.status = "failed"
        request_row.failure_category = category[:80]
        _commit(db)


@router.post("/generate", response_model=EmailDraftResponse)
async def generate_draft(
    payload: EmailGenerationRequest,
    db: Annotated[Session, Depends(get_db)],
    provider: Annotated[AIEmailProvider, Depends(get_ai_provider)],
) -> EmailDraftResponse:
    existing_draft = None
    if payload.draft_id is not None:
        existing_draft = _owned_draft(payload.draft_id, db)
        _ensure_unsent(existing_draft, db)
        if existing_draft.conversation_id is not None:
            raise HTTPException(
                status_code=409,
                detail="Use refinement to revise an existing follow-up draft.",
            )
    purpose = _owned_purpose(payload.purpose_id, db)
    resume = _owned_resume(payload.resume_id, db)
    profile = _professional_profile(db)
    profile_context, profile_fingerprint, links = _profile_generation_state(payload, db)

    context = _generation_context(
        payload=payload,
        sender_name=profile.full_name,
        purpose=purpose,
        profile_context=profile_context,
    )
    try:
        generated = validate_generated_email(
            context, await provider.generate_email(context)
        )
    except AIRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except AITimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except AIUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AIMalformedResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if payload.draft_id is None:
        draft = EmailDraft(owner_id=_owner_id(), subject=generated.subject, body=append_signature_links(generated.body, links))
        db.add(draft)
    else:
        draft = _owned_draft(payload.draft_id, db, for_update=True)
        _ensure_unsent(draft, db)
        _invalidate_pending_reviews(draft.id, db)
        draft.subject = generated.subject
        draft.body = append_signature_links(generated.body, links)
    draft.profile_fingerprint = profile_fingerprint
    draft.included_profile_links = links
    _apply_context(draft, payload)
    _commit(db)
    db.refresh(draft)
    return _draft_response(draft, db)


@router.post("/generate-and-send", response_model=AutomaticEmailSendResult)
async def generate_and_send_automatically(
    payload: AutomaticEmailGenerationRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    provider: Annotated[AIEmailProvider, Depends(get_ai_provider)],
    gmail_service: Annotated[GmailService, Depends(get_gmail_service)],
) -> AutomaticEmailSendResult:
    owner_id = _owner_id()
    fingerprint = _automatic_request_fingerprint(payload)
    existing = db.scalar(
        select(AutomaticSendRequest).where(
            AutomaticSendRequest.owner_id == owner_id,
            AutomaticSendRequest.idempotency_key == payload.idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(
                status_code=409,
                detail="This idempotency key was already used for different email details.",
            )
        stored = _stored_automatic_result(existing, db)
        if stored is not None:
            if stored.status == "uncertain":
                response.status_code = 202
            return stored
        if existing.status == "failed":
            raise HTTPException(
                status_code=409,
                detail="This one-click request already failed. Review the error before starting a new request.",
            )
        raise HTTPException(
            status_code=409,
            detail="This one-click request is already in progress. Do not submit it again.",
        )

    try:
        connection = validate_automatic_initial_preflight(db, owner_id)
    except AutomaticSendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    purpose = _owned_purpose(payload.purpose_id, db)
    resume = _owned_resume(payload.resume_id, db)
    profile = _professional_profile(db)
    profile_context, profile_fingerprint, links = _profile_generation_state(payload, db)

    purpose_snapshot = (purpose.title, purpose.description, purpose.updated_at)
    gmail_identity = (connection.email.lower(), connection.google_subject)
    resume_checksum = None
    if resume is not None:
        path = _storage_path(resume.storage_key)
        if not path.is_file():
            raise HTTPException(status_code=422, detail="The selected resume file is unavailable.")
        try:
            resume_bytes = path.read_bytes()
            resume_checksum = hashlib.sha256(resume_bytes).hexdigest()
        except OSError as exc:
            raise HTTPException(status_code=422, detail="The selected resume file is unavailable.") from exc

    context = _generation_context(
        payload=payload,
        sender_name=profile.full_name,
        purpose=purpose,
        profile_context=profile_context,
    )
    request_row = AutomaticSendRequest(
        owner_id=owner_id,
        idempotency_key=payload.idempotency_key,
        request_fingerprint=fingerprint,
        status="generating",
    )
    db.add(request_row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This one-click request is already in progress. Do not submit it again.",
        ) from exc
    db.refresh(request_row)
    request_id = request_row.id

    try:
        generated = validate_generated_email(
            context, await provider.generate_email(context)
        )
    except AIRateLimitError as exc:
        _fail_automatic_request(db, request_id, category="ai_rate_limit")
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except AITimeoutError as exc:
        _fail_automatic_request(db, request_id, category="ai_timeout")
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except AIUnavailableError as exc:
        _fail_automatic_request(db, request_id, category="ai_unavailable")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AIMalformedResponseError as exc:
        _fail_automatic_request(db, request_id, category="ai_malformed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    db.expire_all()
    live_purpose = db.scalar(
        select(EmailPurpose).where(
            EmailPurpose.id == payload.purpose_id,
            EmailPurpose.owner_id == owner_id,
        )
    )
    live_profile_snapshot = profile_snapshot(db, owner_id, payload.job_description)
    if live_purpose is None or (
        live_purpose.title,
        live_purpose.description,
        live_purpose.updated_at,
    ) != purpose_snapshot:
        _fail_automatic_request(db, request_id, category="purpose_changed")
        raise HTTPException(status_code=409, detail="The selected email purpose changed during generation.")
    if snapshot_fingerprint(live_profile_snapshot) != profile_fingerprint:
        _fail_automatic_request(db, request_id, category="sender_profile_changed")
        raise HTTPException(status_code=409, detail="The sender profile changed during generation.")
    if resume is not None:
        live_resume = db.scalar(
            select(Resume).where(
                Resume.id == payload.resume_id,
                Resume.owner_id == owner_id,
            )
        )
        if live_resume is None:
            _fail_automatic_request(db, request_id, category="resume_unavailable")
            raise HTTPException(status_code=409, detail="The selected resume became unavailable.")
        try:
            path = _storage_path(live_resume.storage_key)
            current_checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, HTTPException) as exc:
            _fail_automatic_request(db, request_id, category="resume_unavailable")
            raise HTTPException(status_code=409, detail="The selected resume became unavailable.") from exc
        if current_checksum != resume_checksum:
            _fail_automatic_request(db, request_id, category="resume_changed")
            raise HTTPException(status_code=409, detail="The selected resume changed during generation.")
    try:
        live_connection = validate_automatic_initial_preflight(db, owner_id)
    except AutomaticSendingError as exc:
        _fail_automatic_request(db, request_id, category="automatic_precondition")
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if (live_connection.email.lower(), live_connection.google_subject) != gmail_identity:
        _fail_automatic_request(db, request_id, category="gmail_identity_changed")
        raise HTTPException(status_code=409, detail="The Gmail sender identity changed during generation.")

    draft = EmailDraft(
        owner_id=owner_id, subject=generated.subject,
        body=append_signature_links(generated.body, links),
        profile_fingerprint=profile_fingerprint, included_profile_links=links,
    )
    db.add(draft)
    _apply_context(draft, payload)
    try:
        db.flush()
        authorization = authorize_automatic_draft(
            db, owner_id, draft.id, payload.recipient_email
        )
        request_row = db.get(AutomaticSendRequest, request_id)
        request_row.draft_id = draft.id
        request_row.authorization_id = authorization.id
        request_row.status = "dispatching"
        _commit(db)
    except AutomaticSendingError as exc:
        _fail_automatic_request(db, request_id, category="authorization_failed")
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        _fail_automatic_request(db, request_id, category="authorization_persist_failed")
        raise

    try:
        result = await run_authorized_send(
            authorization.id,
            AutomaticSendDispatch(run_authorized_send=True),
            response,
            db,
            gmail_service,
        )
    except HTTPException as exc:
        _fail_automatic_request(db, request_id, category=f"dispatch_http_{exc.status_code}")
        raise
    request_row = db.get(AutomaticSendRequest, request_id)
    request_row.send_id = result.id
    request_row.status = result.status
    _commit(db)
    return AutomaticEmailSendResult(
        **result.model_dump(), idempotency_key=payload.idempotency_key
    )


@router.post("/{draft_id}/refine", response_model=EmailRevisionResponse)
async def refine_draft(
    draft_id: UUID,
    payload: EmailRefinementRequest,
    db: Annotated[Session, Depends(get_db)],
    provider: Annotated[AIEmailProvider, Depends(get_ai_provider)],
) -> EmailRevisionResponse:
    draft = _owned_draft(draft_id, db)
    _ensure_unsent(draft, db)
    root = _validate_followup_payload(draft, payload, db)
    purpose = _owned_purpose(payload.purpose_id, db)
    resume = _owned_resume(payload.resume_id, db)
    profile = _professional_profile(db)
    profile_context, _, links = _profile_generation_state(payload, db)

    conversation_history: tuple[dict[str, str], ...] = ()
    is_reply = draft.reply_to_received_message_id is not None
    if root is not None:
        sent_messages = [root, *list(db.scalars(
            select(EmailSend)
            .where(
                EmailSend.owner_id == _owner_id(),
                EmailSend.conversation_id == root.id,
                EmailSend.status == "sent",
                EmailSend.deleted_at.is_(None),
            )
            .order_by(EmailSend.sent_at.asc())
        ))]
        if is_reply:
            _bound_received(draft, root, db)
            received_messages = list(
                db.scalars(
                    select(ReceivedGmailMessage)
                    .where(
                        ReceivedGmailMessage.owner_id == _owner_id(),
                        ReceivedGmailMessage.conversation_id == root.id,
                        ReceivedGmailMessage.gmail_thread_id == root.gmail_thread_id,
                    )
                    .order_by(ReceivedGmailMessage.received_at.asc())
                )
            )
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
                for item in received_messages
            )
            combined.sort(key=lambda item: item[0])
            conversation_history = tuple(item[1] for item in combined[-12:])
        else:
            conversation_history = tuple(
                {"subject": item.subject[:200], "body": item.body[:3000]}
                for item in sent_messages[-10:]
            )
    context = _generation_context(
        payload=payload,
        sender_name=profile.full_name,
        purpose=purpose,
        profile_context=profile_context,
        current_subject=payload.subject,
        current_body=remove_signature_links(payload.body, draft.included_profile_links or {}),
        refinement_instruction=payload.instruction,
        conversation_history=conversation_history,
        is_followup=root is not None and not is_reply,
        is_reply=is_reply,
    )
    try:
        revision = validate_generated_email(
            context, await provider.generate_email(context)
        )
    except AIRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except AITimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except AIUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AIMalformedResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    _owned_draft(draft_id, db)
    return EmailRevisionResponse(
        subject=_reply_subject(root.subject) if root is not None else revision.subject,
        body=append_signature_links(revision.body, links),
    )


@router.get("", response_model=list[EmailDraftResponse])
def list_drafts(db: Annotated[Session, Depends(get_db)]) -> list[EmailDraftResponse]:
    drafts = list(
        db.scalars(
            select(EmailDraft)
            .where(
                EmailDraft.owner_id == _owner_id(),
                EmailDraft.deleted_at.is_(None),
            )
            .order_by(EmailDraft.updated_at.desc())
        )
    )
    return [_draft_response(draft, db) for draft in drafts]


@router.get("/{draft_id}", response_model=EmailDraftResponse)
def get_draft(
    draft_id: UUID, db: Annotated[Session, Depends(get_db)]
) -> EmailDraftResponse:
    return _draft_response(_owned_draft(draft_id, db), db)


@router.put("/{draft_id}", response_model=EmailDraftResponse)
def update_draft(
    draft_id: UUID,
    payload: EmailDraftUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> EmailDraftResponse:
    _owned_purpose(payload.purpose_id, db)
    _owned_resume(payload.resume_id, db)
    draft = _owned_draft(draft_id, db, for_update=True)
    _ensure_unsent(draft, db)
    _validate_followup_payload(draft, payload, db)
    _invalidate_pending_reviews(draft.id, db)
    revoke_draft_authorizations(
        db, _owner_id(), draft.id, reason="draft_changed"
    )
    _, profile_fingerprint, links = _profile_generation_state(payload, db)
    _apply_context(draft, payload)
    draft.subject = payload.subject
    draft.body = append_signature_links(
        remove_signature_links(payload.body, draft.included_profile_links or {}), links
    )
    draft.profile_fingerprint = profile_fingerprint
    draft.included_profile_links = links
    _commit(db)
    db.refresh(draft)
    return _draft_response(draft, db)


@router.delete("/{draft_id}", status_code=204)
def delete_draft(draft_id: UUID, db: Annotated[Session, Depends(get_db)]) -> Response:
    draft = _owned_draft(draft_id, db, for_update=True, include_deleted=True)
    if draft.deleted_at is not None:
        return Response(status_code=204)
    sends = list(
        db.scalars(
            select(EmailSend)
            .where(
                EmailSend.draft_id == draft.id,
                EmailSend.owner_id == _owner_id(),
            )
            .with_for_update()
        )
    )
    if any(send.status == "sending" for send in sends):
        raise HTTPException(
            status_code=409,
            detail="This draft cannot be deleted while Gmail delivery is in progress.",
        )
    for send in sends:
        if send.status == "pending":
            send.status = "stale"
    revoke_draft_authorizations(
        db, _owner_id(), draft.id, reason="draft_deleted"
    )
    draft.deleted_at = datetime.now(timezone.utc)
    _commit(db)
    return Response(status_code=204)
