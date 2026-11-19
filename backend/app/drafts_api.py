from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select, update
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
from app.models import EmailDraft, EmailPurpose, EmailSend, Resume, SenderProfile
from app.resume_text import ResumeExtractionError, extract_resume_text
from app.schemas import (
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
    return response.model_copy(update={"refinement_allowed": _refinement_allowed(draft, db)})


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


def _validate_followup_payload(
    draft: EmailDraft, payload: EmailDraftUpdate | EmailRefinementRequest, db: Session
) -> EmailSend | None:
    root = _followup_root(draft, db)
    if root is None:
        return None
    if payload.recipient_email != root.recipient_email:
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
    profile: SenderProfile,
    purpose: EmailPurpose,
    resume_text: str | None,
    current_subject: str | None = None,
    current_body: str | None = None,
    refinement_instruction: str | None = None,
    conversation_history: tuple[dict[str, str], ...] = (),
    is_followup: bool = False,
) -> EmailGenerationContext:
    return EmailGenerationContext(
        sender_name=profile.name,
        recipient_name=payload.recipient_name,
        company=payload.company,
        role=payload.role,
        job_description=payload.job_description,
        purpose_title=purpose.title,
        purpose_description=purpose.description,
        resume_text=resume_text,
        resume_selected=payload.resume_id is not None,
        attach_resume=payload.attach_resume,
        current_subject=current_subject,
        current_body=current_body,
        refinement_instruction=refinement_instruction,
        conversation_history=conversation_history,
        is_followup=is_followup,
    )


@router.post("/generate", response_model=EmailDraftResponse)
async def generate_draft(
    payload: EmailGenerationRequest,
    db: Annotated[Session, Depends(get_db)],
    provider: Annotated[AIEmailProvider, Depends(get_ai_provider)],
) -> EmailDraft:
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
    profile = db.scalar(
        select(SenderProfile).where(SenderProfile.owner_id == _owner_id())
    )
    if profile is None or not profile.name.strip():
        raise HTTPException(status_code=422, detail="Set a sender name in Settings first.")

    resume_text = None
    if resume is not None:
        path = _storage_path(resume.storage_key)
        if not path.is_file():
            raise HTTPException(status_code=422, detail="The selected resume file is unavailable.")
        try:
            resume_text = extract_resume_text(path)
        except ResumeExtractionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    context = _generation_context(
        payload=payload,
        profile=profile,
        purpose=purpose,
        resume_text=resume_text,
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

    if payload.draft_id is None:
        draft = EmailDraft(owner_id=_owner_id(), subject=generated.subject, body=generated.body)
        db.add(draft)
    else:
        draft = _owned_draft(payload.draft_id, db, for_update=True)
        _ensure_unsent(draft, db)
        _invalidate_pending_reviews(draft.id, db)
        draft.subject = generated.subject
        draft.body = generated.body
    _apply_context(draft, payload)
    _commit(db)
    db.refresh(draft)
    return draft


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
    profile = db.scalar(
        select(SenderProfile).where(SenderProfile.owner_id == _owner_id())
    )
    if profile is None or not profile.name.strip():
        raise HTTPException(status_code=422, detail="Set a sender name in Settings first.")

    resume_text = None
    if resume is not None:
        path = _storage_path(resume.storage_key)
        if not path.is_file():
            raise HTTPException(status_code=422, detail="The selected resume file is unavailable.")
        try:
            resume_text = extract_resume_text(path)
        except ResumeExtractionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    conversation_history: tuple[dict[str, str], ...] = ()
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
        conversation_history = tuple(
            {"subject": item.subject[:200], "body": item.body[:3000]}
            for item in sent_messages[-10:]
        )
    context = _generation_context(
        payload=payload,
        profile=profile,
        purpose=purpose,
        resume_text=resume_text,
        current_subject=payload.subject,
        current_body=payload.body,
        refinement_instruction=payload.instruction,
        conversation_history=conversation_history,
        is_followup=root is not None,
    )
    try:
        revision = await provider.generate_email(context)
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
        body=revision.body,
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
) -> EmailDraft:
    _owned_purpose(payload.purpose_id, db)
    _owned_resume(payload.resume_id, db)
    draft = _owned_draft(draft_id, db, for_update=True)
    _ensure_unsent(draft, db)
    _validate_followup_payload(draft, payload, db)
    _invalidate_pending_reviews(draft.id, db)
    _apply_context(draft, payload)
    draft.subject = payload.subject
    draft.body = payload.body
    _commit(db)
    db.refresh(draft)
    return draft


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
    draft.deleted_at = datetime.now(timezone.utc)
    _commit(db)
    return Response(status_code=204)

