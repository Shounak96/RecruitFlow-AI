from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai_provider import (
    AIMalformedResponseError,
    AIRateLimitError,
    AITimeoutError,
    AIUnavailableError,
    GeneratedEmail,
    get_ai_provider,
)
from app.config import get_settings
from app.main import app
from app.models import EmailDraft, EmailPurpose, EmailSend, Resume, SenderProfile
from tests.api_client import request


REVISED_BODY = "Hi Taylor,\n\nCould we discuss the role briefly?\n\nBest,\nJordan"


class RefinementProvider:
    def __init__(self, error: Exception | None = None, body: str = REVISED_BODY) -> None:
        self.error = error
        self.body = body
        self.contexts = []

    async def generate_email(self, context):
        self.contexts.append(context)
        if self.error:
            raise self.error
        return GeneratedEmail(subject="A shorter introduction", body=self.body)


def seed_owned_draft(db: Session) -> tuple[EmailDraft, EmailPurpose]:
    owner = get_settings().local_owner_id
    profile = SenderProfile(owner_id=owner, name="Jordan")
    purpose = EmailPurpose(
        owner_id=owner,
        title="Introduction",
        description="Introduce me and ask for a short conversation.",
        is_default=True,
    )
    db.add_all([profile, purpose])
    db.flush()
    draft = EmailDraft(
        owner_id=owner,
        purpose_id=purpose.id,
        recipient_name="Taylor",
        recipient_email="taylor@example.com",
        company="Example Co",
        role="Platform Engineer",
        job_description="Build reliable Java services.",
        attach_resume=False,
        subject="Saved subject",
        body="Hi Taylor,\n\nSaved body.\n\nBest,\nJordan",
    )
    db.add(draft)
    db.commit()
    return draft, purpose


def refinement_payload(purpose_id, **overrides):
    payload = {
        "recipient_name": "Taylor",
        "recipient_email": "taylor@example.com",
        "company": "Example Co",
        "role": "Platform Engineer",
        "job_description": "Build reliable Java services.",
        "purpose_id": str(purpose_id),
        "resume_id": None,
        "attach_resume": False,
        "subject": "Unsaved manual subject",
        "body": "Hi Taylor,\n\nUnsaved manual body.\n\nBest,\nJordan",
        "instruction": "Make it shorter and friendlier.",
    }
    payload.update(overrides)
    return payload


def test_refinement_uses_unsaved_content_grounded_context_without_persisting_or_sending(
    db_session: Session, monkeypatch
) -> None:
    draft, purpose = seed_owned_draft(db_session)
    storage_key = f"{uuid4().hex}.pdf"
    path = get_settings().resume_storage_path.resolve() / storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4\n%%EOF")
    resume = Resume(
        owner_id=get_settings().local_owner_id,
        display_name="Java resume",
        original_filename="resume.pdf",
        storage_key=storage_key,
        size_bytes=15,
        is_default=True,
    )
    db_session.add(resume)
    db_session.commit()
    monkeypatch.setattr(
        "app.resume_text.extract_resume_text",
        lambda path: (_ for _ in ()).throw(AssertionError("PDF extraction must not run")),
    )
    provider = RefinementProvider()
    app.dependency_overrides[get_ai_provider] = lambda: provider

    response = request(
        "POST",
        f"/api/drafts/{draft.id}/refine",
        json=refinement_payload(
            purpose.id,
            resume_id=str(resume.id),
            attach_resume=True,
            instruction="Include my relevant Java experience and make it shorter.",
        ),
    )
    assert response.status_code == 200
    assert response.json() == {
        "subject": "A shorter introduction",
        "body": REVISED_BODY,
    }
    context = provider.contexts[0]
    assert context.current_subject == "Unsaved manual subject"
    assert context.current_body.startswith("Hi Taylor")
    assert context.refinement_instruction.startswith("Include my relevant Java")
    assert context.profile_context is not None
    assert context.purpose_description == purpose.description
    assert context.attach_resume is True
    db_session.expire_all()
    assert db_session.get(EmailDraft, draft.id).subject == "Saved subject"
    assert db_session.scalar(select(func.count()).select_from(EmailSend)) == 0


@pytest.mark.parametrize("instruction", ["   ", "x" * 1001])
def test_refinement_rejects_invalid_instruction(
    db_session: Session, instruction: str
) -> None:
    draft, purpose = seed_owned_draft(db_session)
    provider = RefinementProvider()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    response = request(
        "POST",
        f"/api/drafts/{draft.id}/refine",
        json=refinement_payload(purpose.id, instruction=instruction),
    )
    assert response.status_code == 422
    assert provider.contexts == []


def test_refinement_enforces_draft_owner_isolation(db_session: Session) -> None:
    _, purpose = seed_owned_draft(db_session)
    other = EmailDraft(
        owner_id=uuid4(),
        purpose_id=purpose.id,
        recipient_name="Other",
        recipient_email="other@example.com",
        attach_resume=False,
        subject="Private",
        body="Private body",
    )
    db_session.add(other)
    db_session.commit()
    app.dependency_overrides[get_ai_provider] = lambda: RefinementProvider()
    response = request(
        "POST", f"/api/drafts/{other.id}/refine", json=refinement_payload(purpose.id)
    )
    assert response.status_code == 404


@pytest.mark.parametrize("send_status", ["sending", "uncertain"])
def test_refinement_rejects_active_or_uncertain_draft(
    db_session: Session, send_status: str
) -> None:
    draft, purpose = seed_owned_draft(db_session)
    send = EmailSend(
        owner_id=get_settings().local_owner_id,
        draft_id=draft.id,
        status=send_status,
        draft_fingerprint="f" * 64,
        sender_email="verified@example.com",
        recipient_email=draft.recipient_email,
        subject=draft.subject,
        body=draft.body,
    )
    db_session.add(send)
    db_session.commit()
    provider = RefinementProvider()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    response = request(
        "POST", f"/api/drafts/{draft.id}/refine", json=refinement_payload(purpose.id)
    )
    assert response.status_code == 409
    assert provider.contexts == []
    assert request("GET", f"/api/drafts/{draft.id}").json()["refinement_allowed"] is False


def test_refinement_rejects_sent_draft(db_session: Session) -> None:
    draft, purpose = seed_owned_draft(db_session)
    draft.sent_at = datetime.now(timezone.utc)
    db_session.commit()
    provider = RefinementProvider()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    response = request(
        "POST", f"/api/drafts/{draft.id}/refine", json=refinement_payload(purpose.id)
    )
    assert response.status_code == 409
    assert provider.contexts == []
    assert request("GET", f"/api/drafts/{draft.id}").json()["refinement_allowed"] is False


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (AIRateLimitError("rate limited"), 429),
        (AITimeoutError("timeout"), 504),
        (AIUnavailableError("unavailable"), 503),
        (AIMalformedResponseError("malformed"), 502),
    ],
)
def test_refinement_maps_provider_failures_without_changing_draft(
    db_session: Session, error: Exception, status_code: int
) -> None:
    draft, purpose = seed_owned_draft(db_session)
    app.dependency_overrides[get_ai_provider] = lambda: RefinementProvider(error)
    response = request(
        "POST", f"/api/drafts/{draft.id}/refine", json=refinement_payload(purpose.id)
    )
    assert response.status_code == status_code
    db_session.expire_all()
    assert db_session.get(EmailDraft, draft.id).subject == "Saved subject"
    assert db_session.scalar(select(func.count()).select_from(EmailSend)) == 0


def test_initial_outreach_refinement_rejects_metric_revision(
    db_session: Session,
) -> None:
    draft, purpose = seed_owned_draft(db_session)
    provider = RefinementProvider(
        body="Hi Taylor,\n\nI improved throughput by 25%.\n\nBest,\nJordan"
    )
    app.dependency_overrides[get_ai_provider] = lambda: provider

    response = request(
        "POST", f"/api/drafts/{draft.id}/refine", json=refinement_payload(purpose.id)
    )

    assert response.status_code == 502
    assert "quantitative achievement metrics" in response.json()["detail"]
    db_session.expire_all()
    assert db_session.get(EmailDraft, draft.id).subject == "Saved subject"
    assert db_session.scalar(select(func.count()).select_from(EmailSend)) == 0
