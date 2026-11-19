import asyncio

import httpx

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.gmail_service import (
    GMAIL_READONLY_SCOPE,
    GmailMessageResult,
    GmailUpstreamError,
    get_gmail_service,
)
from app.main import app
from app.models import (
    AutomaticSendAuthorization,
    AutomaticSendClaim,
    EmailDraft,
    EmailPurpose,
    EmailSend,
    GmailConnection,
    Resume,
)
from tests.api_client import request
from tests.test_automatic_sending_stage_a import authorize, enable_automation, seed_draft
from tests.test_gmail_replies import gmail_message
from tests.test_history_followups import seed_conversation


class AutomaticGmailService:
    def __init__(self, *, thread=None, send_error=None, read_error=None) -> None:
        self.thread = thread
        self.send_error = send_error
        self.read_error = read_error
        self.send_calls: list[tuple[str, str | None]] = []
        self.read_calls: list[str] = []

    def decrypt_token(self, value: str) -> str:
        return "access-token"

    async def get_thread(self, access_token: str, thread_id: str) -> dict:
        assert access_token == "access-token"
        self.read_calls.append(thread_id)
        if self.read_error:
            raise self.read_error
        return self.thread

    async def send_message(
        self, access_token: str, raw_message: str, thread_id: str | None = None
    ) -> GmailMessageResult:
        assert access_token == "access-token"
        self.send_calls.append((raw_message, thread_id))
        if self.send_error:
            raise self.send_error
        return GmailMessageResult(
            message_id=f"automatic-{len(self.send_calls)}",
            thread_id=thread_id or "automatic-thread",
        )


class BlockingAutomaticGmailService(AutomaticGmailService):
    def __init__(self, started: asyncio.Event, release: asyncio.Event) -> None:
        super().__init__()
        self.started = started
        self.release = release

    async def send_message(
        self, access_token: str, raw_message: str, thread_id: str | None = None
    ) -> GmailMessageResult:
        self.send_calls.append((raw_message, thread_id))
        self.started.set()
        await self.release.wait()
        return GmailMessageResult(message_id="automatic-concurrent", thread_id="automatic-thread")


def run_authorized(authorization_id: str):
    return request(
        "POST",
        f"/api/gmail/automatic-sends/{authorization_id}",
        json={"run_authorized_send": True},
    )


def seed_followup(db: Session) -> tuple[EmailSend, EmailDraft]:
    root, original = seed_conversation(db)
    followup = EmailDraft(
        owner_id=get_settings().local_owner_id,
        purpose_id=original.purpose_id,
        conversation_id=root.id,
        recipient_name=original.recipient_name,
        recipient_email=root.recipient_email,
        company=original.company,
        role=original.role,
        attach_resume=False,
        subject=f"Re: {root.subject}",
        body="Hi Taylor,\n\nFollowing up briefly.\n\nBest,\nJordan",
    )
    db.add(followup)
    db.commit()
    return root, followup


def test_initial_authorize_preview_and_user_triggered_dispatch(
    db_session: Session,
) -> None:
    draft = seed_draft(db_session)
    service = AutomaticGmailService()
    app.dependency_overrides[get_gmail_service] = lambda: service
    assert enable_automation(limit=11).status_code == 200

    preview = request(
        "GET", f"/api/settings/automatic-authorizations/preview/{draft.id}"
    )
    assert preview.status_code == 200
    assert preview.json()["authorization_status"] == "eligible"
    assert preview.json()["sender_email"] == "verified@example.com"
    assert preview.json()["recipient_email"] == draft.recipient_email
    assert preview.json()["body"] == draft.body
    assert preview.json()["remaining_today"] == 11
    assert service.send_calls == []

    created = authorize(draft)
    assert created.status_code == 201
    authorization_id = created.json()["id"]
    assert service.send_calls == []
    sent = run_authorized(authorization_id)
    assert sent.status_code == 200
    assert sent.json()["status"] == "sent"
    assert len(service.send_calls) == 1
    assert service.read_calls == []
    assert service.send_calls[0][1] is None

    db_session.expire_all()
    assert db_session.scalar(select(AutomaticSendClaim)).status == "sent"
    persisted = db_session.scalar(select(EmailSend).where(EmailSend.draft_id == draft.id))
    assert persisted.status == "sent"
    assert db_session.get(EmailDraft, draft.id).sent_at is not None
    repeated = run_authorized(authorization_id)
    assert repeated.status_code == 409
    assert len(service.send_calls) == 1


def test_dispatch_default_off_changed_draft_and_attachment_binding(
    db_session: Session,
) -> None:
    draft = seed_draft(db_session)
    service = AutomaticGmailService()
    app.dependency_overrides[get_gmail_service] = lambda: service
    assert authorize(draft).status_code == 409
    enable_automation()
    authorization_id = authorize(draft).json()["id"]
    authorization = db_session.get(AutomaticSendAuthorization, authorization_id)
    authorization.attachment_sha256 = "f" * 64
    db_session.commit()
    blocked = run_authorized(authorization_id)
    assert blocked.status_code == 409
    assert service.send_calls == []
    db_session.expire_all()
    assert db_session.get(AutomaticSendAuthorization, authorization_id).revoked_at is not None


def test_attachment_content_change_invalidates_authorization(
    db_session: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "resume_storage_path", tmp_path)
    draft = seed_draft(db_session)
    original = b"%PDF-1.4\noriginal-safe-test-pdf"
    changed = b"%PDF-1.4\nmodified-safe-test-pdf"
    assert len(original) == len(changed)
    storage_key = "a" * 32 + ".pdf"
    (tmp_path / storage_key).write_bytes(original)
    resume = Resume(
        owner_id=get_settings().local_owner_id,
        display_name="Test resume",
        original_filename="resume.pdf",
        storage_key=storage_key,
        size_bytes=len(original),
        is_default=True,
    )
    db_session.add(resume)
    db_session.flush()
    draft.resume_id = resume.id
    draft.attach_resume = True
    db_session.commit()
    service = AutomaticGmailService()
    app.dependency_overrides[get_gmail_service] = lambda: service
    enable_automation()
    authorization_id = authorize(draft).json()["id"]
    (tmp_path / storage_key).write_bytes(changed)
    blocked = run_authorized(authorization_id)
    assert blocked.status_code == 409
    assert "attachment changed" in blocked.json()["detail"]
    assert service.send_calls == []


def test_uncertain_dispatch_consumes_claim_and_never_retries(
    db_session: Session,
) -> None:
    draft = seed_draft(db_session)
    service = AutomaticGmailService(
        send_error=GmailUpstreamError(
            "ambiguous", category="dispatch_timeout", uncertain=True
        )
    )
    app.dependency_overrides[get_gmail_service] = lambda: service
    enable_automation()
    authorization_id = authorize(draft).json()["id"]
    result = run_authorized(authorization_id)
    assert result.status_code == 202
    assert result.json()["status"] == "uncertain"
    db_session.expire_all()
    assert db_session.scalar(select(AutomaticSendClaim)).status == "uncertain"
    assert db_session.scalar(select(EmailSend)).status == "uncertain"
    assert run_authorized(authorization_id).status_code == 409
    assert len(service.send_calls) == 1


def test_concurrent_run_requests_dispatch_only_once(db_session: Session) -> None:
    draft = seed_draft(db_session)
    enable_automation()
    authorization_id = authorize(draft).json()["id"]

    async def run_requests():
        started = asyncio.Event()
        release = asyncio.Event()
        service = BlockingAutomaticGmailService(started, release)
        app.dependency_overrides[get_gmail_service] = lambda: service
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first_task = asyncio.create_task(
                client.post(
                    f"/api/gmail/automatic-sends/{authorization_id}",
                    json={"run_authorized_send": True},
                )
            )
            await started.wait()
            second = await client.post(
                f"/api/gmail/automatic-sends/{authorization_id}",
                json={"run_authorized_send": True},
            )
            release.set()
            first = await first_task
        return first, second, service

    first, second, service = asyncio.run(run_requests())
    assert first.status_code == 200
    assert second.status_code == 409
    assert len(service.send_calls) == 1


def test_definitive_dispatch_failure_releases_daily_allowance(
    db_session: Session,
) -> None:
    draft = seed_draft(db_session)
    service = AutomaticGmailService(
        send_error=GmailUpstreamError("rejected", category="dispatch_http_400")
    )
    app.dependency_overrides[get_gmail_service] = lambda: service
    enable_automation(limit=1)
    authorization_id = authorize(draft).json()["id"]
    result = run_authorized(authorization_id)
    assert result.status_code == 502
    db_session.expire_all()
    assert db_session.scalar(select(AutomaticSendClaim)).status == "released"
    preview = request(
        "GET", f"/api/settings/automatic-authorizations/preview/{draft.id}"
    )
    assert preview.status_code == 200
    assert preview.json()["remaining_today"] == 1
    assert len(service.send_calls) == 1


def test_safe_followup_reads_only_linked_thread_and_sends_in_same_thread(
    db_session: Session,
) -> None:
    root, followup = seed_followup(db_session)
    service = AutomaticGmailService(
        thread={"id": root.gmail_thread_id, "messages": [{"id": root.gmail_message_id}]}
    )
    app.dependency_overrides[get_gmail_service] = lambda: service
    enable_automation(initial=False, followups=True)
    authorization_id = authorize(followup).json()["id"]
    result = run_authorized(authorization_id)
    assert result.status_code == 200
    assert service.read_calls == [root.gmail_thread_id]
    assert len(service.send_calls) == 1
    assert service.send_calls[0][1] == root.gmail_thread_id


def test_followup_blocks_when_recipient_replied_without_sending(
    db_session: Session,
) -> None:
    root, followup = seed_followup(db_session)
    service = AutomaticGmailService(
        thread={
            "id": root.gmail_thread_id,
            "messages": [
                {"id": root.gmail_message_id},
                gmail_message("new-reply", thread_id=root.gmail_thread_id),
            ],
        }
    )
    app.dependency_overrides[get_gmail_service] = lambda: service
    enable_automation(initial=False, followups=True)
    authorization_id = authorize(followup).json()["id"]
    blocked = run_authorized(authorization_id)
    assert blocked.status_code == 409
    assert "recipient replied" in blocked.json()["detail"]
    assert service.send_calls == []
    assert db_session.scalar(select(AutomaticSendClaim)) is None


def test_followup_blocks_missing_scope_and_read_failure(
    db_session: Session,
) -> None:
    root, followup = seed_followup(db_session)
    connection = db_session.scalar(select(GmailConnection))
    connection.scopes = " ".join(
        scope for scope in connection.scopes.split() if scope != GMAIL_READONLY_SCOPE
    )
    db_session.commit()
    service = AutomaticGmailService(thread={"id": root.gmail_thread_id, "messages": []})
    app.dependency_overrides[get_gmail_service] = lambda: service
    enable_automation(initial=False, followups=True)
    first_id = authorize(followup).json()["id"]
    missing = run_authorized(first_id)
    assert missing.status_code == 409
    assert "read-only" in missing.json()["detail"]
    assert service.read_calls == [] and service.send_calls == []

    connection.scopes = f"{connection.scopes} {GMAIL_READONLY_SCOPE}".strip()
    db_session.commit()
    request("DELETE", f"/api/settings/automatic-authorizations/{first_id}")
    second_id = authorize(followup).json()["id"]
    service.read_error = GmailUpstreamError("timeout", category="thread_timeout")
    failed = run_authorized(second_id)
    assert failed.status_code == 503
    assert service.send_calls == []
