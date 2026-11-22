from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import (
    GmailConnection, ProfessionalProfile, ProfileEducation, ProfileExperience,
    ProfileProject, ProfileSkillGroup, SenderProfile,
)
from app.profile_service import load_profile_rows
from app.schemas import ProfessionalProfileResponse, ProfessionalProfileUpdate

router = APIRouter(prefix="/api/profile", tags=["profile"])


def _owner_id(): return get_settings().local_owner_id


def _ensure(db: Session) -> ProfessionalProfile:
    profile = db.scalar(select(ProfessionalProfile).where(ProfessionalProfile.owner_id == _owner_id()))
    if profile is None:
        legacy = db.scalar(select(SenderProfile).where(SenderProfile.owner_id == _owner_id()))
        profile = ProfessionalProfile(owner_id=_owner_id(), full_name=legacy.name if legacy else "")
        db.add(profile); db.flush()
    return profile


def _response(db: Session) -> ProfessionalProfileResponse:
    profile = _ensure(db)
    _, education, experiences, skills, projects = load_profile_rows(db, _owner_id())
    connection = db.scalar(select(GmailConnection).where(GmailConnection.owner_id == _owner_id(), GmailConnection.is_valid.is_(True)))
    return ProfessionalProfileResponse(
        full_name=profile.full_name, headline=profile.headline, email=profile.email,
        location=profile.location, summary=profile.summary,
        experience_duration=profile.experience_duration, github_url=profile.github_url,
        linkedin_url=profile.linkedin_url, portfolio_url=profile.portfolio_url,
        custom_links=profile.custom_links or [], version=profile.version,
        updated_at=profile.updated_at, verified_gmail_email=connection.email if connection else None,
        education=[{"degree": x.degree, "field": x.field, "institution": x.institution,
                    "graduation_status": x.graduation_status, "start_date": x.start_date, "end_date": x.end_date} for x in education],
        experiences=[{"company": x.company, "role": x.role, "employment_type": x.employment_type,
                      "start_date": x.start_date, "end_date": x.end_date, "is_current": x.is_current,
                      "work_summary": x.work_summary, "technologies": x.technologies} for x in experiences],
        skill_groups=[{"category": x.category, "skills": x.skills} for x in skills],
        projects=[{"title": x.title, "technologies": x.technologies,
                   "description": x.description} for x in projects],
    )


@router.get("", response_model=ProfessionalProfileResponse)
def get_profile(db: Annotated[Session, Depends(get_db)]):
    _ensure(db); db.commit(); return _response(db)


@router.put("", response_model=ProfessionalProfileResponse)
def update_profile(payload: ProfessionalProfileUpdate, db: Annotated[Session, Depends(get_db)]):
    profile = _ensure(db)
    for key in ("full_name", "headline", "email", "location", "summary", "experience_duration",
                "github_url", "linkedin_url", "portfolio_url", "custom_links"):
        value = getattr(payload, key)
        setattr(profile, key, [x.model_dump() for x in value] if key == "custom_links" else value)
    profile.version += 1
    legacy = db.scalar(select(SenderProfile).where(SenderProfile.owner_id == _owner_id()))
    if legacy is None:
        legacy = SenderProfile(owner_id=_owner_id(), name=payload.full_name or "Local user"); db.add(legacy)
    elif payload.full_name:
        legacy.name = payload.full_name
    for model in (ProfileEducation, ProfileExperience, ProfileSkillGroup, ProfileProject):
        db.execute(delete(model).where(model.owner_id == _owner_id()))
    for position, item in enumerate(payload.education):
        db.add(ProfileEducation(owner_id=_owner_id(), profile_id=profile.id, position=position, **item.model_dump()))
    for position, item in enumerate(payload.experiences):
        db.add(ProfileExperience(owner_id=_owner_id(), profile_id=profile.id, position=position, **item.model_dump()))
    for position, item in enumerate(payload.skill_groups):
        db.add(ProfileSkillGroup(owner_id=_owner_id(), profile_id=profile.id, position=position, **item.model_dump()))
    for position, item in enumerate(payload.projects):
        db.add(ProfileProject(owner_id=_owner_id(), profile_id=profile.id, position=position, **item.model_dump()))
    try:
        db.commit()
    except Exception as exc:
        db.rollback(); raise HTTPException(status_code=409, detail="The professional profile could not be saved.") from exc
    return _response(db)
