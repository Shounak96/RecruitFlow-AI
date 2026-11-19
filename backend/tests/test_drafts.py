from uuid import uuid4

import pytest
from sqlalchemy import select
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
from app.resume_text import ResumeExtractionError
from tests.api_client import request


GENERATED_BODY = (
    "Hi Taylor,\n\n"
    "I build reliable APIs and would like to discuss the platform role.\n\n"
    "Would you be available for a brief conversation next week?\n\n"
    "Best,\nJordan"
)


class FakeProvider:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.contexts = []

    async def generate_email(self, context):
        self.contexts.append(context)
        if self.error:
            raise self.error
        return GeneratedEmail(subject="A brief introduction", body=GENERATED_BODY)


def seed_context(db: Session):
    owner = get_settings().local_owner_id
    profile = SenderProfile(owner_id=owner, name="Jordan")
    purpose = EmailPurpose(
        owner_id=owner,
        title="Introduction",
        description="Introduce me and ask for a brief conversation.",
        is_default=True,
    )
    db.add_all([profile, purpose])
    db.commit()
    return purpose


def generation_payload(purpose_id, **overrides):
    payload = {
        "recipient_name": "Taylor",
        "recipient_email": "taylor@example.com",
        "company": "Example Co",
        "role": "Platform Engineer",
        "job_description": "Build reliable APIs.",
        "purpose_id": str(purpose_id),
        "resume_id": None,
        "attach_resume": False,
    }
    payload.update(overrides)
    return payload


def test_generate_persists_grounded_context_and_draft_crud(db_session: Session) -> None:
    purpose = seed_context(db_session)
    provider = FakeProvider()
    app.dependency_overrides[get_ai_provider] = lambda: provider

    generated = request("POST", "/api/drafts/generate", json=generation_payload(purpose.id))
    assert generated.status_code == 200
    data = generated.json()
    assert data["status"] == "not_sent"
    assert data["subject"] == "A brief introduction"
    assert data["body"] == GENERATED_BODY
    assert provider.contexts[0].purpose_description == purpose.description
    assert provider.contexts[0].job_description == "Build reliable APIs."
    assert provider.contexts[0].resume_text is None
    assert provider.contexts[0].resume_selected is False
    assert provider.contexts[0].attach_resume is False

    draft_id = data["id"]
    persisted = db_session.get(EmailDraft, draft_id)
    assert persisted is not None
    assert persisted.owner_id == get_settings().local_owner_id
    assert persisted.body == GENERATED_BODY
    assert request("GET", f"/api/drafts/{draft_id}").json()["body"] == GENERATED_BODY

    updated_payload = generation_payload(purpose.id)
    edited_body = "Hi Taylor,\n\nEdited first paragraph.\n\nRegards,\nJordan"
    updated_payload.update({"subject": "Edited subject", "body": edited_body})
    updated = request("PUT", f"/api/drafts/{draft_id}", json=updated_payload)
    assert updated.status_code == 200
    assert updated.json()["body"] == edited_body
    reloaded = request("GET", f"/api/drafts/{draft_id}").json()
    assert reloaded["subject"] == "Edited subject"
    assert reloaded["body"] == edited_body
    listed = request("GET", "/api/drafts").json()
    assert len(listed) == 1
    assert listed[0]["body"] == edited_body

    regenerated = request(
        "POST",
        "/api/drafts/generate",
        json=generation_payload(purpose.id, draft_id=draft_id),
    )
    assert regenerated.status_code == 200
    assert len(request("GET", "/api/drafts").json()) == 1

    assert request("DELETE", f"/api/drafts/{draft_id}").status_code == 204
    assert request("DELETE", f"/api/drafts/{draft_id}").status_code == 204
    assert request("GET", f"/api/drafts/{draft_id}").status_code == 404
    db_session.expire_all()
    assert db_session.get(EmailDraft, draft_id).deleted_at is not None


def test_delete_draft_stales_review_and_blocks_all_draft_actions(
    db_session: Session,
) -> None:
    purpose = seed_context(db_session)
    draft = EmailDraft(
        owner_id=get_settings().local_owner_id,
        purpose_id=purpose.id,
        recipient_name="Taylor",
        recipient_email="taylor@example.com",
        attach_resume=False,
        subject="Draft to remove",
        body=GENERATED_BODY,
    )
    db_session.add(draft)
    db_session.flush()
    review = EmailSend(
        owner_id=draft.owner_id,
        draft_id=draft.id,
        status="pending",
        draft_fingerprint="f" * 64,
        sender_email="verified@example.com",
        recipient_email=draft.recipient_email,
        subject=draft.subject,
        body=draft.body,
    )
    db_session.add(review)
    db_session.commit()

    assert request("DELETE", f"/api/drafts/{draft.id}").status_code == 204
    db_session.expire_all()
    assert db_session.get(EmailDraft, draft.id).deleted_at is not None
    assert db_session.get(EmailSend, review.id).status == "stale"
    assert request("GET", "/api/drafts").json() == []
    assert request("GET", f"/api/drafts/{draft.id}").status_code == 404

    update_payload = generation_payload(purpose.id)
    update_payload.update({"subject": "Changed", "body": GENERATED_BODY})
    assert request("PUT", f"/api/drafts/{draft.id}", json=update_payload).status_code == 404
    provider = FakeProvider()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    assert request(
        "POST",
        "/api/drafts/generate",
        json=generation_payload(purpose.id, draft_id=str(draft.id)),
    ).status_code == 404
    assert provider.contexts == []


def test_delete_draft_rejects_active_dispatch(db_session: Session) -> None:
    purpose = seed_context(db_session)
    draft = EmailDraft(
        owner_id=get_settings().local_owner_id,
        purpose_id=purpose.id,
        recipient_name="Taylor",
        recipient_email="taylor@example.com",
        attach_resume=False,
        subject="Sending draft",
        body=GENERATED_BODY,
    )
    db_session.add(draft)
    db_session.flush()
    db_session.add(
        EmailSend(
            owner_id=draft.owner_id,
            draft_id=draft.id,
            status="sending",
            draft_fingerprint="f" * 64,
            sender_email="verified@example.com",
            recipient_email=draft.recipient_email,
            subject=draft.subject,
            body=draft.body,
        )
    )
    db_session.commit()

    response = request("DELETE", f"/api/drafts/{draft.id}")
    assert response.status_code == 409
    db_session.expire_all()
    assert db_session.get(EmailDraft, draft.id).deleted_at is None


def test_draft_owner_isolation(db_session: Session) -> None:
    purpose = seed_context(db_session)
    other = EmailDraft(
        owner_id=uuid4(),
        purpose_id=purpose.id,
        recipient_name="Other",
        recipient_email="other@example.com",
        attach_resume=False,
        subject="Private",
        body="Other owner's draft",
    )
    db_session.add(other)
    db_session.commit()
    assert request("GET", "/api/drafts").json() == []
    assert request("GET", f"/api/drafts/{other.id}").status_code == 404
    assert request("DELETE", f"/api/drafts/{other.id}").status_code == 404
    assert request("DELETE", "/api/drafts/not-a-uuid").status_code == 422


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (AIRateLimitError("rate limited"), 429),
        (AITimeoutError("timeout"), 504),
        (AIUnavailableError("unavailable"), 503),
        (AIMalformedResponseError("malformed"), 502),
    ],
)
def test_generation_maps_provider_failures(db_session: Session, error, status_code) -> None:
    purpose = seed_context(db_session)
    app.dependency_overrides[get_ai_provider] = lambda: FakeProvider(error)
    response = request("POST", "/api/drafts/generate", json=generation_payload(purpose.id))
    assert response.status_code == status_code
    assert db_session.scalar(select(EmailDraft)) is None


def test_resume_grounding_and_extraction_failure(db_session: Session, monkeypatch) -> None:
    purpose = seed_context(db_session)
    storage_key = f"{uuid4().hex}.pdf"
    path = get_settings().resume_storage_path.resolve() / storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4\n%%EOF")
    resume = Resume(
        owner_id=get_settings().local_owner_id,
        display_name="Resume",
        original_filename="resume.pdf",
        storage_key=storage_key,
        size_bytes=15,
        is_default=True,
    )
    db_session.add(resume)
    db_session.commit()

    provider = FakeProvider()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    monkeypatch.setattr("app.drafts_api.extract_resume_text", lambda path: "Verified resume facts")
    response = request(
        "POST",
        "/api/drafts/generate",
        json=generation_payload(purpose.id, resume_id=str(resume.id), attach_resume=True),
    )
    assert response.status_code == 200
    assert provider.contexts[0].resume_text == "Verified resume facts"
    assert provider.contexts[0].resume_selected is True
    assert provider.contexts[0].attach_resume is True
    assert response.json()["attach_resume"] is True

    monkeypatch.setattr(
        "app.drafts_api.extract_resume_text",
        lambda path: (_ for _ in ()).throw(ResumeExtractionError("image-only PDF")),
    )
    failed = request(
        "POST",
        "/api/drafts/generate",
        json=generation_payload(purpose.id, resume_id=str(resume.id)),
    )
    assert failed.status_code == 422
    assert failed.json()["detail"] == "image-only PDF"


def test_generation_validation_and_owned_relations(db_session: Session) -> None:
    purpose = seed_context(db_session)
    app.dependency_overrides[get_ai_provider] = lambda: FakeProvider()
    invalid_email = request(
        "POST",
        "/api/drafts/generate",
        json=generation_payload(purpose.id, recipient_email="not-an-email"),
    )
    assert invalid_email.status_code == 422
    missing_resume = request(
        "POST",
        "/api/drafts/generate",
        json=generation_payload(purpose.id, attach_resume=True),
    )
    assert missing_resume.status_code == 422
    other_purpose = EmailPurpose(
        owner_id=uuid4(), title="Other", description="Private", is_default=True
    )
    db_session.add(other_purpose)
    db_session.commit()
    assert request(
        "POST", "/api/drafts/generate", json=generation_payload(other_purpose.id)
    ).status_code == 404

