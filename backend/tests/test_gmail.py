import asyncio
import base64
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.gmail_service import (
    GMAIL_SCOPES,
    GmailCredentialError,
    GmailMessageResult,
    GmailService,
    GmailUpstreamError,
    OAuthGrant,
    build_plain_text_message,
    get_gmail_service,
)
from app.main import app
from app.models import (
    EmailDraft,
    EmailPurpose,
    EmailSend,
    GmailConnection,
    GmailOAuthState,
    Resume,
)
from tests.api_client import request


class FakeGmailService:
    def __init__(self, send_error: GmailUpstreamError | None = None) -> None:
        self.send_error = send_error
        self.sent_messages: list[str] = []

    def decrypt_token(self, value: str) -> str:
        return "access-token"

    async def send_message(self, access_token: str, raw_message: str) -> GmailMessageResult:
        assert access_token == "access-token"
        self.sent_messages.append(raw_message)
        if self.send_error:
            raise self.send_error
        return GmailMessageResult(message_id="message-123", thread_id="thread-456")


class FakeOAuthService(FakeGmailService):
    def __init__(self) -> None:
        super().__init__()
        self.key = Fernet.generate_key()

    def authorization_url(self, state: str) -> str:
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}"

    async def complete_oauth(self, code: str) -> OAuthGrant:
        assert code == "test-code"
        return OAuthGrant(
            access_token="new-access-token",
            refresh_token="new-refresh-token",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            scopes=frozenset(GMAIL_SCOPES),
            google_subject="google-subject",
            email="verified@example.com",
        )

    def encrypt_token(self, token: str) -> str:
        return Fernet(self.key).encrypt(token.encode()).decode()

    async def revoke(self, token: str) -> None:
        return None


class BlockingGmailService(FakeGmailService):
    def __init__(self, started: asyncio.Event, release: asyncio.Event) -> None:
        super().__init__()
        self.started = started
        self.release = release

    async def send_message(self, access_token: str, raw_message: str) -> GmailMessageResult:
        self.sent_messages.append(raw_message)
        self.started.set()
        await self.release.wait()
        return GmailMessageResult(message_id="message-123", thread_id="thread-456")


@pytest.fixture
def gmail_config(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "gmail_oauth_client_id", "client-id")
    monkeypatch.setattr(settings, "gmail_oauth_client_secret", SecretStr("client-secret"))
    monkeypatch.setattr(settings, "gmail_token_encryption_key", SecretStr(Fernet.generate_key().decode()))
    monkeypatch.setattr(
        settings,
        "gmail_oauth_redirect_uri",
        "http://localhost:8001/api/gmail/oauth/callback",
    )
    return settings


def seed_draft(db: Session) -> EmailDraft:
    owner = get_settings().local_owner_id
    purpose = EmailPurpose(
        owner_id=owner,
        title="Introduction",
        description="Ask for a short conversation.",
        is_default=True,
    )
    db.add(purpose)
    db.flush()
    draft = EmailDraft(
        owner_id=owner,
        purpose_id=purpose.id,
        recipient_name="Taylor",
        recipient_email="taylor@example.com",
        attach_resume=False,
        subject="Platform engineering conversation",
        body="Hi Taylor,\n\nWould you have time to talk?\n\nBest,\nJordan",
    )
    db.add(draft)
    db.commit()
    return draft


def seed_connection(db: Session) -> GmailConnection:
    connection = GmailConnection(
        owner_id=get_settings().local_owner_id,
        google_subject="google-subject",
        email="verified@example.com",
        encrypted_access_token="encrypted-access",
        encrypted_refresh_token="encrypted-refresh",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=" ".join(GMAIL_SCOPES),
        is_valid=True,
    )
    db.add(connection)
    db.commit()
    return connection


def test_authorization_url_uses_exact_scopes_offline_access_and_redirect(gmail_config) -> None:
    query = parse_qs(urlparse(GmailService().authorization_url("csrf-state")).query)
    assert query["scope"] == [" ".join(GMAIL_SCOPES)]
    assert query["redirect_uri"] == ["http://localhost:8001/api/gmail/oauth/callback"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["state"] == ["csrf-state"]


def test_oauth_state_is_hashed_one_time_and_tokens_are_encrypted(db_session: Session) -> None:
    service = FakeOAuthService()
    app.dependency_overrides[get_gmail_service] = lambda: service
    authorize = request("GET", "/api/gmail/oauth/authorize")
    assert authorize.status_code == 200
    state = parse_qs(urlparse(authorize.json()["authorization_url"]).query)["state"][0]
    stored_state = db_session.scalar(select(GmailOAuthState))
    assert stored_state is not None and stored_state.state_hash != state

    callback = request(
        "GET", f"/api/gmail/oauth/callback?state={state}&code=test-code", follow_redirects=False
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "http://localhost:3000/settings?gmail=connected"
    db_session.expire_all()
    connection = db_session.scalar(select(GmailConnection))
    assert connection is not None
    assert connection.email == "verified@example.com"
    assert "new-access-token" not in connection.encrypted_access_token
    assert "new-refresh-token" not in connection.encrypted_refresh_token
    assert db_session.scalar(select(GmailOAuthState)) is None
    assert request(
        "GET", f"/api/gmail/oauth/callback?state={state}&code=test-code", follow_redirects=False
    ).status_code == 400


def test_mime_plain_text_and_pdf_attachment_preserve_content() -> None:
    body = "Hi Taylor,\n\nA concise paragraph.\n\nBest,\nJordan"
    raw = build_plain_text_message(
        sender_email="verified@example.com",
        recipient_email="taylor@example.com",
        subject="Hello",
        body=body,
        attachment=("Jordan Resume.pdf", b"%PDF-1.4\n%%EOF"),
    )
    parsed = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
    assert parsed["From"] == "verified@example.com"
    assert parsed["To"] == "taylor@example.com"
    assert parsed["Subject"] == "Hello"
    parts = list(parsed.iter_attachments())
    assert parsed.get_body(preferencelist=("plain",)).get_content().replace("\r\n", "\n").rstrip("\n") == body
    assert len(parts) == 1 and parts[0].get_filename() == "Jordan Resume.pdf"
    assert parts[0].get_payload(decode=True) == b"%PDF-1.4\n%%EOF"


def test_review_approval_send_persistence_duplicate_guard_and_read_only(
    db_session: Session,
) -> None:
    draft = seed_draft(db_session)
    seed_connection(db_session)
    service = FakeGmailService()
    app.dependency_overrides[get_gmail_service] = lambda: service

    review = request("POST", f"/api/gmail/reviews/{draft.id}")
    assert review.status_code == 201
    snapshot = review.json()
    assert snapshot["sender_email"] == "verified@example.com"
    assert snapshot["body"] == draft.body
    assert snapshot["attachment"] is None
    assert service.sent_messages == []

    sent = request(
        "POST", f"/api/gmail/sends/{snapshot['id']}", json={"approved": True}
    )
    assert sent.status_code == 200
    assert sent.json()["status"] == "sent"
    assert len(service.sent_messages) == 1
    assert request(
        "POST", f"/api/gmail/sends/{snapshot['id']}", json={"approved": True}
    ).status_code == 409
    assert len(service.sent_messages) == 1
    db_session.expire_all()
    persisted = db_session.get(EmailSend, snapshot["id"])
    assert persisted.status == "sent"
    assert persisted.gmail_message_id == "message-123"
    assert persisted.gmail_thread_id == "thread-456"
    assert db_session.get(EmailDraft, draft.id).sent_at is not None
    history = request("GET", "/api/gmail/history").json()
    assert history[0]["gmail_thread_id"] == "thread-456"
    assert request("DELETE", f"/api/drafts/{draft.id}").status_code == 409


def test_draft_edit_invalidates_review_and_blocks_send(db_session: Session) -> None:
    draft = seed_draft(db_session)
    seed_connection(db_session)
    service = FakeGmailService()
    app.dependency_overrides[get_gmail_service] = lambda: service
    review_id = request("POST", f"/api/gmail/reviews/{draft.id}").json()["id"]
    payload = {
        "recipient_name": draft.recipient_name,
        "recipient_email": draft.recipient_email,
        "company": None,
        "role": None,
        "job_description": None,
        "purpose_id": str(draft.purpose_id),
        "resume_id": None,
        "attach_resume": False,
        "subject": "Edited subject",
        "body": draft.body,
    }
    assert request("PUT", f"/api/drafts/{draft.id}", json=payload).status_code == 200
    assert request(
        "POST", f"/api/gmail/sends/{review_id}", json={"approved": True}
    ).status_code == 409
    assert service.sent_messages == []


def test_review_binds_exact_attachment_bytes(db_session: Session) -> None:
    draft = seed_draft(db_session)
    seed_connection(db_session)
    first_content = b"%PDF-1.4\nA\n%%EOF"
    changed_content = b"%PDF-1.4\nB\n%%EOF"
    storage_key = f"{uuid4().hex}.pdf"
    path = get_settings().resume_storage_path.resolve() / storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(first_content)
    resume = Resume(
        owner_id=get_settings().local_owner_id,
        display_name="Resume",
        original_filename="Jordan Resume.pdf",
        storage_key=storage_key,
        size_bytes=len(first_content),
        is_default=True,
    )
    db_session.add(resume)
    db_session.flush()
    draft.resume_id = resume.id
    draft.attach_resume = True
    db_session.commit()
    service = FakeGmailService()
    app.dependency_overrides[get_gmail_service] = lambda: service

    review = request("POST", f"/api/gmail/reviews/{draft.id}")
    assert review.status_code == 201
    assert review.json()["attachment"] == {
        "filename": "Jordan Resume.pdf",
        "size_bytes": len(first_content),
    }
    persisted = db_session.get(EmailSend, review.json()["id"])
    assert persisted.attachment_sha256 is not None

    path.write_bytes(changed_content)
    result = request(
        "POST", f"/api/gmail/sends/{review.json()['id']}", json={"approved": True}
    )
    assert result.status_code == 409
    assert service.sent_messages == []
    db_session.expire_all()
    assert db_session.get(EmailSend, review.json()["id"]).status == "stale"


def test_send_requires_literal_explicit_approval(db_session: Session) -> None:
    draft = seed_draft(db_session)
    seed_connection(db_session)
    service = FakeGmailService()
    app.dependency_overrides[get_gmail_service] = lambda: service
    review_id = request("POST", f"/api/gmail/reviews/{draft.id}").json()["id"]
    assert request("POST", f"/api/gmail/sends/{review_id}", json={}).status_code == 422
    assert request(
        "POST", f"/api/gmail/sends/{review_id}", json={"approved": False}
    ).status_code == 422
    assert service.sent_messages == []


def test_concurrent_approval_clicks_dispatch_only_once(db_session: Session) -> None:
    draft = seed_draft(db_session)
    seed_connection(db_session)
    review_id = request("POST", f"/api/gmail/reviews/{draft.id}").json()["id"]

    async def run_requests():
        started = asyncio.Event()
        release = asyncio.Event()
        service = BlockingGmailService(started, release)
        app.dependency_overrides[get_gmail_service] = lambda: service
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first_task = asyncio.create_task(
                client.post(f"/api/gmail/sends/{review_id}", json={"approved": True})
            )
            await started.wait()
            edit = await client.put(
                f"/api/drafts/{draft.id}",
                json={
                    "recipient_name": draft.recipient_name,
                    "recipient_email": draft.recipient_email,
                    "company": None,
                    "role": None,
                    "job_description": None,
                    "purpose_id": str(draft.purpose_id),
                    "resume_id": None,
                    "attach_resume": False,
                    "subject": "Edit attempted during dispatch",
                    "body": draft.body,
                },
            )
            second = await client.post(
                f"/api/gmail/sends/{review_id}", json={"approved": True}
            )
            release.set()
            first = await first_task
        return first, second, edit, service

    first, second, edit, service = asyncio.run(run_requests())
    assert first.status_code == 200
    assert second.status_code == 409
    assert edit.status_code == 409
    assert len(service.sent_messages) == 1


@pytest.mark.parametrize(
    ("error", "expected_status", "http_status"),
    [
        (GmailUpstreamError("timeout", category="dispatch_timeout", uncertain=True), "uncertain", 202),
        (GmailUpstreamError("rejected", category="dispatch_http_500"), "failed", 502),
    ],
)
def test_send_failures_are_durable_and_never_retried(
    db_session: Session, error, expected_status: str, http_status: int
) -> None:
    draft = seed_draft(db_session)
    seed_connection(db_session)
    service = FakeGmailService(error)
    app.dependency_overrides[get_gmail_service] = lambda: service
    review_id = request("POST", f"/api/gmail/reviews/{draft.id}").json()["id"]
    result = request("POST", f"/api/gmail/sends/{review_id}", json={"approved": True})
    assert result.status_code == http_status
    db_session.expire_all()
    assert db_session.get(EmailSend, review_id).status == expected_status
    assert request(
        "POST", f"/api/gmail/sends/{review_id}", json={"approved": True}
    ).status_code == 409
    assert len(service.sent_messages) == 1


def test_google_oauth_exchange_validates_verified_identity_and_scopes(gmail_config) -> None:
    calls = []

    def handler(request_: httpx.Request) -> httpx.Response:
        calls.append(request_)
        if "token" in str(request_.url):
            return httpx.Response(
                200,
                json={
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 3600,
                    "scope": " ".join(GMAIL_SCOPES),
                },
            )
        return httpx.Response(
            200, json={"sub": "subject", "email": "verified@example.com", "email_verified": True}
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await GmailService(client).complete_oauth("authorization-code")

    grant = asyncio.run(run())
    assert grant.email == "verified@example.com"
    assert len(calls) == 2
    assert b"redirect_uri=http%3A%2F%2Flocalhost%3A8001%2Fapi%2Fgmail%2Foauth%2Fcallback" in calls[0].content


def test_oauth_rejects_missing_scope_or_unverified_identity(gmail_config) -> None:
    def missing_scope(request_: httpx.Request) -> httpx.Response:
        if "token" in str(request_.url):
            return httpx.Response(
                200,
                json={
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 3600,
                    "scope": "openid https://www.googleapis.com/auth/userinfo.email",
                },
            )
        raise AssertionError("userinfo must not be requested without all required scopes")

    async def run_missing_scope():
        async with httpx.AsyncClient(transport=httpx.MockTransport(missing_scope)) as client:
            await GmailService(client).complete_oauth("authorization-code")

    with pytest.raises(GmailCredentialError):
        asyncio.run(run_missing_scope())

    def unverified(request_: httpx.Request) -> httpx.Response:
        if "token" in str(request_.url):
            return httpx.Response(
                200,
                json={
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 3600,
                    "scope": " ".join(GMAIL_SCOPES),
                },
            )
        return httpx.Response(
            200, json={"sub": "subject", "email": "unverified@example.com", "email_verified": False}
        )

    async def run_unverified():
        async with httpx.AsyncClient(transport=httpx.MockTransport(unverified)) as client:
            await GmailService(client).complete_oauth("authorization-code")

    with pytest.raises(GmailCredentialError):
        asyncio.run(run_unverified())


def test_disconnect_revokes_and_removes_local_credentials(db_session: Session) -> None:
    seed_connection(db_session)

    class RevokingService(FakeGmailService):
        revoked: list[str] = []

        async def revoke(self, token: str) -> None:
            self.revoked.append(token)

    service = RevokingService()
    app.dependency_overrides[get_gmail_service] = lambda: service
    response = request("DELETE", "/api/gmail/connection")
    assert response.status_code == 204
    assert service.revoked == ["access-token"]
    db_session.expire_all()
    assert db_session.scalar(select(GmailConnection)) is None


def test_revoked_refresh_token_invalidates_connection_and_prevents_dispatch(
    db_session: Session,
) -> None:
    draft = seed_draft(db_session)
    connection = seed_connection(db_session)
    connection.token_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()

    class RevokedRefreshService(FakeGmailService):
        async def refresh_access_token(self, refresh_token: str):
            raise GmailCredentialError("revoked")

    service = RevokedRefreshService()
    app.dependency_overrides[get_gmail_service] = lambda: service
    review_id = request("POST", f"/api/gmail/reviews/{draft.id}").json()["id"]
    response = request(
        "POST", f"/api/gmail/sends/{review_id}", json={"approved": True}
    )
    assert response.status_code == 409
    assert service.sent_messages == []
    db_session.expire_all()
    assert db_session.get(GmailConnection, connection.id).is_valid is False


@pytest.mark.parametrize(
    "transport_error",
    [
        httpx.ReadError("connection closed while reading"),
        httpx.WriteError("connection closed while writing"),
        httpx.RemoteProtocolError("invalid response after dispatch"),
    ],
)
def test_ambiguous_dispatch_transport_errors_are_uncertain(
    gmail_config, transport_error: httpx.HTTPError
) -> None:
    def handler(request_: httpx.Request) -> httpx.Response:
        raise transport_error

    async def run_send():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await GmailService(client).send_message("access", "raw")

    with pytest.raises(GmailUpstreamError) as raised:
        asyncio.run(run_send())
    assert raised.value.uncertain is True
    assert raised.value.category == "dispatch_ambiguous_transport"
