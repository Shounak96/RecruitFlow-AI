import re
from pathlib import PurePosixPath
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import EmailPurpose, GmailConnection, Resume, SenderProfile
from app.schemas import (
    EmailPurposeCreate,
    EmailPurposeResponse,
    EmailPurposeUpdate,
    ResumeResponse,
    SenderProfileResponse,
    SenderProfileUpdate,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])
MAX_RESUME_BYTES = 5 * 1024 * 1024
STORAGE_KEY_PATTERN = re.compile(r"^[0-9a-f]{32}\.pdf$")


def _owner_id() -> UUID:
    return get_settings().local_owner_id


def _ensure_profile(db: Session) -> SenderProfile:
    owner_id = _owner_id()
    profile = db.scalar(select(SenderProfile).where(SenderProfile.owner_id == owner_id))
    if profile is None:
        profile = SenderProfile(owner_id=owner_id, name="Local user")
        db.add(profile)
        db.flush()
    return profile


def _commit(db: Session) -> None:
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The requested change could not be saved.",
        ) from exc


def _storage_root():
    root = get_settings().resume_storage_path.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _storage_path(storage_key: str):
    if not STORAGE_KEY_PATTERN.fullmatch(storage_key):
        raise HTTPException(status_code=500, detail="Stored resume reference is invalid.")
    root = _storage_root()
    path = (root / storage_key).resolve()
    if path.parent != root:
        raise HTTPException(status_code=500, detail="Stored resume reference is invalid.")
    return path


def _safe_original_filename(filename: str | None) -> str:
    normalized = (filename or "resume.pdf").replace("\\", "/").replace("\x00", "")
    safe_name = PurePosixPath(normalized).name.strip()
    return (safe_name or "resume.pdf")[:255]


def _profile_response(profile: SenderProfile, db: Session) -> SenderProfileResponse:
    connection = db.scalar(
        select(GmailConnection).where(GmailConnection.owner_id == _owner_id())
    )
    return SenderProfileResponse(
        name=profile.name,
        gmail_connected=bool(connection and connection.is_valid),
        gmail_email=connection.email if connection else None,
    )


@router.get("/profile", response_model=SenderProfileResponse)
def get_profile(db: Annotated[Session, Depends(get_db)]) -> SenderProfileResponse:
    profile = _ensure_profile(db)
    _commit(db)
    return _profile_response(profile, db)


@router.put("/profile", response_model=SenderProfileResponse)
def update_profile(
    payload: SenderProfileUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> SenderProfileResponse:
    profile = _ensure_profile(db)
    profile.name = payload.name
    _commit(db)
    return _profile_response(profile, db)


@router.get("/resumes", response_model=list[ResumeResponse])
def list_resumes(db: Annotated[Session, Depends(get_db)]) -> list[Resume]:
    return list(
        db.scalars(
            select(Resume)
            .where(Resume.owner_id == _owner_id())
            .order_by(Resume.is_default.desc(), Resume.created_at.asc())
        )
    )


@router.post("/resumes", response_model=ResumeResponse, status_code=201)
async def upload_resume(
    display_name: Annotated[str, Form(min_length=1, max_length=160)],
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
) -> Resume:
    display_name = display_name.strip()
    if not display_name:
        raise HTTPException(status_code=422, detail="Display name cannot be blank.")
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=415, detail="Only PDF resumes are accepted.")

    content = await file.read(MAX_RESUME_BYTES + 1)
    await file.close()
    if len(content) > MAX_RESUME_BYTES:
        raise HTTPException(status_code=413, detail="Resume must be 5 MB or smaller.")
    if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-2048:]:
        raise HTTPException(status_code=422, detail="The uploaded file is not a valid PDF.")

    owner_id = _owner_id()
    storage_key = f"{uuid4().hex}.pdf"
    storage_path = _storage_path(storage_key)
    try:
        with storage_path.open("xb") as stored_file:
            stored_file.write(content)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Resume storage is unavailable.") from exc

    try:
        _ensure_profile(db)
        has_resume = db.scalar(
            select(func.count()).select_from(Resume).where(Resume.owner_id == owner_id)
        )
        resume = Resume(
            owner_id=owner_id,
            display_name=display_name,
            original_filename=_safe_original_filename(file.filename),
            storage_key=storage_key,
            size_bytes=len(content),
            is_default=has_resume == 0,
        )
        db.add(resume)
        _commit(db)
        db.refresh(resume)
        return resume
    except Exception:
        storage_path.unlink(missing_ok=True)
        raise


def _owned_resume(resume_id: UUID, db: Session) -> Resume:
    resume = db.scalar(
        select(Resume).where(Resume.id == resume_id, Resume.owner_id == _owner_id())
    )
    if resume is None:
        raise HTTPException(status_code=404, detail="Resume not found.")
    return resume


@router.get("/resumes/{resume_id}/download")
def download_resume(
    resume_id: UUID, db: Annotated[Session, Depends(get_db)]
) -> FileResponse:
    resume = _owned_resume(resume_id, db)
    path = _storage_path(resume.storage_key)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Resume file not found.")
    return FileResponse(path, media_type="application/pdf", filename=resume.original_filename)


@router.put("/resumes/{resume_id}/default", response_model=ResumeResponse)
def set_default_resume(
    resume_id: UUID, db: Annotated[Session, Depends(get_db)]
) -> Resume:
    resume = _owned_resume(resume_id, db)
    db.execute(
        update(Resume).where(Resume.owner_id == _owner_id()).values(is_default=False)
    )
    resume.is_default = True
    _commit(db)
    db.refresh(resume)
    return resume


@router.delete("/resumes/{resume_id}", status_code=204)
def delete_resume(
    resume_id: UUID, db: Annotated[Session, Depends(get_db)]
) -> Response:
    resume = _owned_resume(resume_id, db)
    was_default = resume.is_default
    path = _storage_path(resume.storage_key)
    db.delete(resume)
    db.flush()
    if was_default:
        replacement = db.scalar(
            select(Resume)
            .where(Resume.owner_id == _owner_id())
            .order_by(Resume.created_at.asc())
            .limit(1)
        )
        if replacement is not None:
            replacement.is_default = True
    _commit(db)
    path.unlink(missing_ok=True)
    return Response(status_code=204)


@router.get("/purposes", response_model=list[EmailPurposeResponse])
def list_purposes(db: Annotated[Session, Depends(get_db)]) -> list[EmailPurpose]:
    return list(
        db.scalars(
            select(EmailPurpose)
            .where(EmailPurpose.owner_id == _owner_id())
            .order_by(EmailPurpose.is_default.desc(), EmailPurpose.created_at.asc())
        )
    )


@router.post("/purposes", response_model=EmailPurposeResponse, status_code=201)
def create_purpose(
    payload: EmailPurposeCreate,
    db: Annotated[Session, Depends(get_db)],
) -> EmailPurpose:
    owner_id = _owner_id()
    _ensure_profile(db)
    has_purpose = db.scalar(
        select(func.count()).select_from(EmailPurpose).where(EmailPurpose.owner_id == owner_id)
    )
    purpose = EmailPurpose(
        owner_id=owner_id,
        title=payload.title,
        description=payload.description,
        is_default=has_purpose == 0,
    )
    db.add(purpose)
    _commit(db)
    db.refresh(purpose)
    return purpose


def _owned_purpose(purpose_id: UUID, db: Session) -> EmailPurpose:
    purpose = db.scalar(
        select(EmailPurpose).where(
            EmailPurpose.id == purpose_id,
            EmailPurpose.owner_id == _owner_id(),
        )
    )
    if purpose is None:
        raise HTTPException(status_code=404, detail="Email purpose not found.")
    return purpose


@router.put("/purposes/{purpose_id}", response_model=EmailPurposeResponse)
def update_purpose(
    purpose_id: UUID,
    payload: EmailPurposeUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> EmailPurpose:
    purpose = _owned_purpose(purpose_id, db)
    purpose.title = payload.title
    purpose.description = payload.description
    _commit(db)
    db.refresh(purpose)
    return purpose


@router.put("/purposes/{purpose_id}/default", response_model=EmailPurposeResponse)
def set_default_purpose(
    purpose_id: UUID, db: Annotated[Session, Depends(get_db)]
) -> EmailPurpose:
    purpose = _owned_purpose(purpose_id, db)
    db.execute(
        update(EmailPurpose)
        .where(EmailPurpose.owner_id == _owner_id())
        .values(is_default=False)
    )
    purpose.is_default = True
    _commit(db)
    db.refresh(purpose)
    return purpose


@router.delete("/purposes/{purpose_id}", status_code=204)
def delete_purpose(
    purpose_id: UUID, db: Annotated[Session, Depends(get_db)]
) -> Response:
    purpose = _owned_purpose(purpose_id, db)
    was_default = purpose.is_default
    db.delete(purpose)
    db.flush()
    if was_default:
        replacement = db.scalar(
            select(EmailPurpose)
            .where(EmailPurpose.owner_id == _owner_id())
            .order_by(EmailPurpose.created_at.asc())
            .limit(1)
        )
        if replacement is not None:
            replacement.is_default = True
    _commit(db)
    return Response(status_code=204)

