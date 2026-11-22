import asyncio
from uuid import uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai_provider import AITimeoutError, GeneratedEmail, get_ai_provider
from app.config import get_settings
from app.gmail_service import GmailUpstreamError, get_gmail_service
from app.main import app
from app.models import (
    AutomaticSendAuthorization,
    AutomaticSendClaim,
    AutomaticSendRequest,
    EmailDraft,
    EmailPurpose,
    EmailSend,
    Resume,
)
from tests.api_client import request
from tests.test_automatic_sending_stage_a import enable_automation, seed_draft
from tests.test_automatic_sending_stage_b import AutomaticGmailService


GENERATED_BODY = "Hi Taylor,\n\nI would value a brief conversation.\n\nBest,\nJordan"


class OneClickProvider:
    def __init__(self, *, error=None, callback=None, body=GENERATED_BODY) -> None:
        self.error = error
        self.callback = callback
        self.body = body
        self.contexts = []

    async def generate_email(self, context):
        self.contexts.append(context)
        if self.callback:
            result = self.callback()
            if asyncio.iscoroutine(result):
                await result
        if self.error:
            raise self.error
        return GeneratedEmail(subject="Generated introduction", body=self.body)


def payload(draft: EmailDraft, *, key=None, **overrides) -> dict:
    value = {
        "idempotency_key": str(key or uuid4()),
        "recipient_name": "Taylor",
        "recipient_email": "taylor@example.com",
        "company": "Example Co",
        "role": "Platform Engineer",
        "job_description": "Build reliable APIs.",
        "purpose_id": str(draft.purpose_id),
        "resume_id": None,
        "attach_resume": False,
    }
    value.update(overrides)
    return value


def test_manual_default_and_opt_out_never_generate_or_send(db_session: Session) -> None:
    draft = seed_draft(db_session)
    provider = OneClickProvider()
    service = AutomaticGmailService()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    app.dependency_overrides[get_gmail_service] = lambda: service
    result = request("POST", "/api/drafts/generate-and-send", json=payload(draft))
    assert result.status_code == 409
    assert provider.contexts == []
    assert service.send_calls == []
    assert db_session.scalar(select(AutomaticSendRequest)) is None


def test_successful_one_click_is_grounded_audited_and_idempotent(
    db_session: Session,
) -> None:
    draft = seed_draft(db_session)
    provider = OneClickProvider()
    service = AutomaticGmailService()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    app.dependency_overrides[get_gmail_service] = lambda: service
    enable_automation(limit=11)
    key = uuid4()
    request_payload = payload(draft, key=key)
    first = request("POST", "/api/drafts/generate-and-send", json=request_payload)
    assert first.status_code == 200
    assert first.json()["status"] == "sent"
    assert first.json()["idempotency_key"] == str(key)
    assert len(provider.contexts) == 1
    assert provider.contexts[0].recipient_name == "Taylor"
    assert provider.contexts[0].purpose_title == "Introduction"
    assert len(service.send_calls) == 1

    db_session.expire_all()
    request_row = db_session.scalar(select(AutomaticSendRequest))
    sent_draft = db_session.get(EmailDraft, request_row.draft_id)
    send = db_session.get(EmailSend, request_row.send_id)
    assert request_row.status == "sent"
    assert sent_draft.recipient_email == "taylor@example.com"
    assert sent_draft.subject == "Generated introduction"
    assert sent_draft.sent_at is not None
    assert send.recipient_email == "taylor@example.com"
    assert send.approved_at is None
    assert db_session.scalar(select(AutomaticSendAuthorization)) is not None
    assert db_session.scalar(select(AutomaticSendClaim)).status == "sent"

    replay = request("POST", "/api/drafts/generate-and-send", json=request_payload)
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert len(provider.contexts) == 1
    assert len(service.send_calls) == 1
    changed = {**request_payload, "recipient_email": "other@example.com"}
    assert request("POST", "/api/drafts/generate-and-send", json=changed).status_code == 409
    assert len(service.send_calls) == 1


def test_invalid_input_and_ai_failure_create_no_claim_or_send(db_session: Session) -> None:
    draft = seed_draft(db_session)
    provider = OneClickProvider(error=AITimeoutError("The AI request timed out."))
    service = AutomaticGmailService()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    app.dependency_overrides[get_gmail_service] = lambda: service
    enable_automation()
    invalid = payload(draft, recipient_email="invalid")
    assert request("POST", "/api/drafts/generate-and-send", json=invalid).status_code == 422
    assert provider.contexts == []
    failed = request("POST", "/api/drafts/generate-and-send", json=payload(draft))
    assert failed.status_code == 504
    db_session.expire_all()
    request_row = db_session.scalar(select(AutomaticSendRequest))
    assert request_row.status == "failed" and request_row.draft_id is None
    assert db_session.scalar(select(AutomaticSendClaim)) is None
    assert db_session.scalar(select(EmailSend)) is None
    assert service.send_calls == []


def test_one_click_rejects_metric_output_before_claim_or_gmail_send(
    db_session: Session,
) -> None:
    draft = seed_draft(db_session)
    provider = OneClickProvider(
        body=(
            "Hi Taylor,\n\nI'm an M.S. in Computer Science candidate at Example "
            "University. I supported 250K+ monthly requests.\n\n"
            "Would you be open to referring me?\n\nBest,\nJordan"
        )
    )
    service = AutomaticGmailService()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    app.dependency_overrides[get_gmail_service] = lambda: service
    enable_automation()

    result = request("POST", "/api/drafts/generate-and-send", json=payload(draft))

    assert result.status_code == 502
    assert "quantitative achievement metrics" in result.json()["detail"]
    db_session.expire_all()
    request_row = db_session.scalar(select(AutomaticSendRequest))
    assert request_row.status == "failed" and request_row.draft_id is None
    assert db_session.scalar(select(AutomaticSendClaim)) is None
    assert db_session.scalar(select(EmailSend)) is None
    assert service.send_calls == []


def test_daily_limit_is_checked_before_second_ai_generation(db_session: Session) -> None:
    draft = seed_draft(db_session)
    provider = OneClickProvider()
    service = AutomaticGmailService()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    app.dependency_overrides[get_gmail_service] = lambda: service
    enable_automation(limit=1)
    assert request("POST", "/api/drafts/generate-and-send", json=payload(draft)).status_code == 200
    blocked = request("POST", "/api/drafts/generate-and-send", json=payload(draft))
    assert blocked.status_code == 409
    assert "daily limit" in blocked.json()["detail"]
    assert len(provider.contexts) == 1
    assert len(service.send_calls) == 1


def test_attachment_change_during_generation_blocks_before_claim(
    db_session: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "resume_storage_path", tmp_path)
    monkeypatch.setattr(
        "app.resume_text.extract_resume_text",
        lambda path: (_ for _ in ()).throw(AssertionError("PDF extraction must not run")),
    )
    draft = seed_draft(db_session)
    first_bytes = b"%PDF-1.4\nA\n%%EOF"
    second_bytes = b"%PDF-1.4\nB\n%%EOF"
    path = tmp_path / ("b" * 32 + ".pdf")
    path.write_bytes(first_bytes)
    resume = Resume(
        owner_id=get_settings().local_owner_id,
        display_name="Resume",
        original_filename="resume.pdf",
        storage_key=path.name,
        size_bytes=len(first_bytes),
        is_default=True,
    )
    db_session.add(resume)
    db_session.commit()
    provider = OneClickProvider(callback=lambda: path.write_bytes(second_bytes))
    service = AutomaticGmailService()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    app.dependency_overrides[get_gmail_service] = lambda: service
    enable_automation()
    result = request(
        "POST",
        "/api/drafts/generate-and-send",
        json=payload(draft, resume_id=str(resume.id), attach_resume=True),
    )
    assert result.status_code == 409
    assert "resume changed" in result.json()["detail"]
    assert db_session.scalar(select(AutomaticSendClaim)) is None
    assert service.send_calls == []


def test_purpose_change_during_generation_blocks_stale_context(
    db_session: Session,
) -> None:
    draft = seed_draft(db_session)

    def change_purpose():
        purpose = db_session.get(EmailPurpose, draft.purpose_id)
        purpose.description = "Changed while generation was in progress."
        db_session.commit()

    provider = OneClickProvider(callback=change_purpose)
    service = AutomaticGmailService()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    app.dependency_overrides[get_gmail_service] = lambda: service
    enable_automation()
    result = request("POST", "/api/drafts/generate-and-send", json=payload(draft))
    assert result.status_code == 409
    assert "purpose changed" in result.json()["detail"]
    assert db_session.scalar(select(AutomaticSendClaim)) is None
    assert service.send_calls == []


def test_uncertain_outcome_is_replayed_without_duplicate_send(db_session: Session) -> None:
    draft = seed_draft(db_session)
    provider = OneClickProvider()
    service = AutomaticGmailService(
        send_error=GmailUpstreamError(
            "ambiguous", category="dispatch_timeout", uncertain=True
        )
    )
    app.dependency_overrides[get_ai_provider] = lambda: provider
    app.dependency_overrides[get_gmail_service] = lambda: service
    enable_automation()
    key = uuid4()
    request_payload = payload(draft, key=key)
    first = request("POST", "/api/drafts/generate-and-send", json=request_payload)
    assert first.status_code == 202
    assert first.json()["status"] == "uncertain"
    replay = request("POST", "/api/drafts/generate-and-send", json=request_payload)
    assert replay.status_code == 202
    assert replay.json()["id"] == first.json()["id"]
    assert len(provider.contexts) == 1
    assert len(service.send_calls) == 1
    db_session.expire_all()
    assert db_session.scalar(select(AutomaticSendRequest)).status == "uncertain"
    assert db_session.scalar(select(AutomaticSendClaim)).status == "uncertain"


def test_concurrent_double_click_generates_and_sends_once(db_session: Session) -> None:
    draft = seed_draft(db_session)
    enable_automation()
    key = uuid4()
    request_payload = payload(draft, key=key)

    async def run_requests():
        started = asyncio.Event()
        release = asyncio.Event()

        async def wait_during_generation():
            started.set()
            await release.wait()

        provider = OneClickProvider(callback=wait_during_generation)
        service = AutomaticGmailService()
        app.dependency_overrides[get_ai_provider] = lambda: provider
        app.dependency_overrides[get_gmail_service] = lambda: service
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first_task = asyncio.create_task(
                client.post("/api/drafts/generate-and-send", json=request_payload)
            )
            await started.wait()
            second = await client.post("/api/drafts/generate-and-send", json=request_payload)
            release.set()
            first = await first_task
        return first, second, provider, service

    first, second, provider, service = asyncio.run(run_requests())
    assert first.status_code == 200
    assert second.status_code == 409
    assert len(provider.contexts) == 1
    assert len(service.send_calls) == 1
    assert db_session.scalar(select(func.count(EmailSend.id))) == 1


def test_one_click_contract_cannot_create_followup_or_received_reply(
    db_session: Session,
) -> None:
    draft = seed_draft(db_session)
    enable_automation(initial=True, followups=True)
    body = payload(draft, conversation_id=str(uuid4()), reply_to_received_message_id=str(uuid4()))
    assert request("POST", "/api/drafts/generate-and-send", json=body).status_code == 422
