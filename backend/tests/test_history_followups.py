import base64
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_provider import GeneratedEmail, get_ai_provider
from app.config import get_settings
from app.gmail_service import GMAIL_SCOPES, GmailMessageResult, GmailUpstreamError, get_gmail_service
from app.main import app
from app.models import EmailDraft, EmailPurpose, EmailSend, GmailConnection, SenderProfile
from tests.api_client import request


class FollowupProvider:
    def __init__(self) -> None:
        self.contexts = []

    async def generate_email(self, context):
        self.contexts.append(context)
        return GeneratedEmail(
            subject="Model subject is ignored",
            body="Hi Taylor,\n\nI wanted to follow up briefly.\n\nBest,\nJordan",
        )


class ThreadGmailService:
    def __init__(self, *, returned_thread: str = "thread-root", error=None) -> None:
        self.returned_thread = returned_thread
        self.error = error
        self.calls = []

    def decrypt_token(self, value: str) -> str:
        return "access-token"

    async def send_message(self, access_token: str, raw_message: str, thread_id=None):
        self.calls.append((raw_message, thread_id))
        if self.error:
            raise self.error
        return GmailMessageResult(
            message_id=f"message-{len(self.calls)}", thread_id=self.returned_thread
        )


def seed_conversation(db: Session) -> tuple[EmailSend, EmailDraft]:
    owner = get_settings().local_owner_id
    profile = SenderProfile(owner_id=owner, name="Jordan")
    purpose = EmailPurpose(
        owner_id=owner,
        title="Introduction",
        description="Ask for a brief conversation without implying any reply.",
        is_default=True,
    )
    db.add_all([profile, purpose])
    db.flush()
    draft = EmailDraft(
        owner_id=owner,
        purpose_id=purpose.id,
        recipient_name="Taylor Recruiter",
        recipient_email="taylor@example.com",
        company="Example Co",
        role="Platform Engineer",
        attach_resume=False,
        subject="Platform engineering conversation",
        body="Hi Taylor,\n\nCould we discuss the role?\n\nBest,\nJordan",
        sent_at=datetime.now(timezone.utc),
    )
    connection = GmailConnection(
        owner_id=owner,
        google_subject="google-subject",
        email="verified@example.com",
        encrypted_access_token="encrypted-access",
        encrypted_refresh_token="encrypted-refresh",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=" ".join(GMAIL_SCOPES),
        is_valid=True,
    )
    db.add_all([draft, connection])
    db.flush()
    root = EmailSend(
        owner_id=owner,
        draft_id=draft.id,
        status="sent",
        draft_fingerprint="a" * 64,
        sender_name="Jordan",
        sender_email="verified@example.com",
        recipient_email="taylor@example.com",
        subject=draft.subject,
        body=draft.body,
        sent_at=draft.sent_at,
        gmail_message_id="gmail-root",
        gmail_thread_id="thread-root",
        rfc_message_id="<root-message@verified.example>",
    )
    db.add(root)
    db.commit()
    return root, draft


def generate_followup(root: EmailSend, provider: FollowupProvider):
    app.dependency_overrides[get_ai_provider] = lambda: provider
    return request(
        "POST",
        "/api/followups/generate",
        json={"conversation_id": str(root.id), "guidance": "Keep it short."},
    )


def test_history_is_owner_scoped_searchable_and_distinguishes_uncertain(
    db_session: Session,
) -> None:
    root, draft = seed_conversation(db_session)
    other_draft = EmailDraft(
        owner_id=uuid4(), purpose_id=draft.purpose_id, recipient_name="Private",
        recipient_email="private@example.com", attach_resume=False,
        subject="Private", body="Private",
    )
    db_session.add(other_draft); db_session.flush()
    other_send = EmailSend(
        owner_id=other_draft.owner_id, draft_id=other_draft.id, status="sent",
        draft_fingerprint="b" * 64, sender_email="other@example.com",
        recipient_email="private@example.com", subject="Private", body="Private",
        sent_at=datetime.now(timezone.utc), gmail_message_id="other", gmail_thread_id="other",
    )
    db_session.add(other_send)
    db_session.commit()
    history = request("GET", "/api/history?search=Example").json()
    assert len(history) == 1
    assert history[0]["id"] == str(root.id)
    assert history[0]["recruiter_name"] == "Taylor Recruiter"
    assert history[0]["delivery_state"] == "sent"
    assert history[0]["can_follow_up"] is True
    assert request("GET", f"/api/history/{other_send.id}").status_code == 404
    assert request("DELETE", f"/api/history/{other_send.id}").status_code == 404
    assert request("DELETE", "/api/history/not-a-uuid").status_code == 422


def test_followup_generation_uses_only_sent_context_and_creates_separate_draft(
    db_session: Session,
) -> None:
    root, original = seed_conversation(db_session)
    provider = FollowupProvider()
    response = generate_followup(root, provider)
    assert response.status_code == 200
    data = response.json()
    assert data["conversation_id"] == str(root.id)
    assert data["subject"] == "Re: Platform engineering conversation"
    assert data["id"] != str(original.id)
    context = provider.contexts[0]
    assert context.is_followup is True
    assert context.followup_guidance == "Keep it short."
    assert context.conversation_history[0]["body"] == root.body
    assert "reply" not in context.conversation_history[0]
    assert db_session.get(EmailDraft, original.id).body == original.body


def test_followup_mime_thread_headers_and_repeated_followup(
    db_session: Session,
) -> None:
    root, _ = seed_conversation(db_session)
    provider = FollowupProvider()
    first_draft = generate_followup(root, provider).json()
    review = request("POST", f"/api/gmail/reviews/{first_draft['id']}").json()
    service = ThreadGmailService()
    app.dependency_overrides[get_gmail_service] = lambda: service
    sent = request("POST", f"/api/gmail/sends/{review['id']}", json={"approved": True})
    assert sent.status_code == 200
    assert request("POST", f"/api/gmail/sends/{review['id']}", json={"approved": True}).status_code == 409
    assert len(service.calls) == 1
    raw, thread_id = service.calls[0]
    message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
    assert thread_id == "thread-root"
    assert message["In-Reply-To"] == "<root-message@verified.example>"
    assert "<root-message@verified.example>" in message["References"]
    assert message["Subject"] == "Re: Platform engineering conversation"
    first_send = db_session.get(EmailSend, review["id"])
    assert first_send.conversation_id == root.id
    assert first_send.gmail_thread_id == "thread-root"

    second_draft = generate_followup(root, provider).json()
    second_review = request("POST", f"/api/gmail/reviews/{second_draft['id']}").json()
    second_send = db_session.get(EmailSend, second_review["id"])
    assert second_send.parent_send_id == first_send.id
    assert second_send.in_reply_to == first_send.rfc_message_id
    assert first_send.rfc_message_id in second_send.references_header


def test_followup_rejects_cross_account_and_stale_review(db_session: Session) -> None:
    root, _ = seed_conversation(db_session)
    provider = FollowupProvider()
    draft = generate_followup(root, provider).json()
    review = request("POST", f"/api/gmail/reviews/{draft['id']}").json()
    competing_draft = EmailDraft(
        owner_id=root.owner_id, purpose_id=db_session.get(EmailDraft, root.draft_id).purpose_id,
        conversation_id=root.id, recipient_name="Taylor Recruiter",
        recipient_email=root.recipient_email, attach_resume=False,
        subject="Re: Platform engineering conversation", body="Competing sent follow-up",
        sent_at=datetime.now(timezone.utc),
    )
    db_session.add(competing_draft); db_session.flush()
    db_session.add(EmailSend(
        owner_id=root.owner_id, draft_id=competing_draft.id, conversation_id=root.id,
        parent_send_id=root.id, status="sent", draft_fingerprint="c" * 64,
        sender_email=root.sender_email, recipient_email=root.recipient_email,
        subject=competing_draft.subject, body=competing_draft.body,
        sent_at=competing_draft.sent_at, gmail_message_id="competing",
        gmail_thread_id=root.gmail_thread_id, rfc_message_id="<competing@example.com>",
    )); db_session.commit()
    service = ThreadGmailService()
    app.dependency_overrides[get_gmail_service] = lambda: service
    assert request("POST", f"/api/gmail/sends/{review['id']}", json={"approved": True}).status_code == 409
    assert service.calls == []

    connection = db_session.scalar(select(GmailConnection))
    connection.email = "different@example.com"; db_session.commit()
    assert generate_followup(root, provider).status_code == 409


def test_followup_thread_mismatch_and_timeout_are_uncertain_and_not_retryable(
    db_session: Session,
) -> None:
    root, _ = seed_conversation(db_session)
    provider = FollowupProvider()
    draft = generate_followup(root, provider).json()
    review = request("POST", f"/api/gmail/reviews/{draft['id']}").json()
    service = ThreadGmailService(returned_thread="wrong-thread")
    app.dependency_overrides[get_gmail_service] = lambda: service
    result = request("POST", f"/api/gmail/sends/{review['id']}", json={"approved": True})
    assert result.status_code == 202
    assert result.json()["status"] == "uncertain"
    db_session.expire_all()
    assert db_session.get(EmailSend, review["id"]).failure_category == "thread_mismatch"
    assert request("POST", f"/api/gmail/sends/{review['id']}", json={"approved": True}).status_code == 409


def test_followup_dispatch_timeout_is_uncertain(db_session: Session) -> None:
    root, _ = seed_conversation(db_session)
    draft = generate_followup(root, FollowupProvider()).json()
    review = request("POST", f"/api/gmail/reviews/{draft['id']}").json()
    service = ThreadGmailService(
        error=GmailUpstreamError("timeout", category="dispatch_timeout", uncertain=True)
    )
    app.dependency_overrides[get_gmail_service] = lambda: service
    result = request("POST", f"/api/gmail/sends/{review['id']}", json={"approved": True})
    assert result.status_code == 202
    assert result.json()["status"] == "uncertain"
    db_session.expire_all()
    assert db_session.get(EmailSend, review["id"]).status == "uncertain"
    detail = request("GET", f"/api/history/{root.id}").json()
    assert detail["messages"][-1]["delivery_state"] == "uncertain"
    assert detail["can_follow_up"] is False
    item = request("GET", "/api/history").json()[0]
    assert item["uncertain_follow_up_count"] == 1


def test_followup_subject_and_recipient_cannot_leave_thread(db_session: Session) -> None:
    root, _ = seed_conversation(db_session)
    draft = generate_followup(root, FollowupProvider()).json()
    payload = {key: draft[key] for key in [
        "recipient_name", "recipient_email", "company", "role", "job_description",
        "purpose_id", "resume_id", "attach_resume", "subject", "body",
    ]}
    payload["recipient_email"] = "other@example.com"
    assert request("PUT", f"/api/drafts/{draft['id']}", json=payload).status_code == 409
    payload["recipient_email"] = root.recipient_email
    payload["subject"] = "Different subject"
    assert request("PUT", f"/api/drafts/{draft['id']}", json=payload).status_code == 409


def test_delete_conversation_tombstones_all_local_records_without_gmail_calls(
    db_session: Session,
) -> None:
    root, original = seed_conversation(db_session)
    sent_draft = EmailDraft(
        owner_id=root.owner_id,
        purpose_id=original.purpose_id,
        conversation_id=root.id,
        recipient_name=original.recipient_name,
        recipient_email=root.recipient_email,
        attach_resume=False,
        subject="Re: Platform engineering conversation",
        body="Confirmed follow-up",
        sent_at=datetime.now(timezone.utc),
    )
    pending_draft = EmailDraft(
        owner_id=root.owner_id,
        purpose_id=original.purpose_id,
        conversation_id=root.id,
        recipient_name=original.recipient_name,
        recipient_email=root.recipient_email,
        attach_resume=False,
        subject="Re: Platform engineering conversation",
        body="Pending follow-up",
    )
    db_session.add_all([sent_draft, pending_draft])
    db_session.flush()
    sent_followup = EmailSend(
        owner_id=root.owner_id,
        draft_id=sent_draft.id,
        conversation_id=root.id,
        parent_send_id=root.id,
        status="sent",
        draft_fingerprint="b" * 64,
        sender_email=root.sender_email,
        recipient_email=root.recipient_email,
        subject=sent_draft.subject,
        body=sent_draft.body,
        sent_at=sent_draft.sent_at,
        gmail_message_id="gmail-followup",
        gmail_thread_id=root.gmail_thread_id,
        rfc_message_id="<followup@verified.example>",
    )
    pending_review = EmailSend(
        owner_id=root.owner_id,
        draft_id=pending_draft.id,
        conversation_id=root.id,
        parent_send_id=sent_followup.id,
        status="pending",
        draft_fingerprint="c" * 64,
        sender_email=root.sender_email,
        recipient_email=root.recipient_email,
        subject=pending_draft.subject,
        body=pending_draft.body,
    )
    db_session.add_all([sent_followup, pending_review])
    db_session.commit()
    service = ThreadGmailService()
    app.dependency_overrides[get_gmail_service] = lambda: service

    assert request("DELETE", f"/api/history/{root.id}").status_code == 204
    assert request("DELETE", f"/api/history/{root.id}").status_code == 204
    assert service.calls == []
    assert request("GET", "/api/history").json() == []
    assert request("GET", f"/api/history/{root.id}").status_code == 404
    assert request("GET", "/api/drafts").json() == []
    assert request("GET", "/api/gmail/history").json() == []
    provider = FollowupProvider()
    assert generate_followup(root, provider).status_code == 404
    assert provider.contexts == []

    db_session.expire_all()
    persisted_root = db_session.get(EmailSend, root.id)
    persisted_followup = db_session.get(EmailSend, sent_followup.id)
    persisted_pending = db_session.get(EmailSend, pending_review.id)
    assert persisted_root.deleted_at is not None
    assert persisted_root.gmail_message_id == "gmail-root"
    assert persisted_root.gmail_thread_id == "thread-root"
    assert persisted_followup.deleted_at is not None
    assert persisted_followup.status == "sent"
    assert persisted_pending.deleted_at is not None
    assert persisted_pending.status == "stale"
    assert all(
        db_session.get(EmailDraft, draft_id).deleted_at is not None
        for draft_id in (original.id, sent_draft.id, pending_draft.id)
    )


def test_delete_uncertain_conversation_preserves_tombstone_and_identifiers(
    db_session: Session,
) -> None:
    root, _ = seed_conversation(db_session)
    root.status = "uncertain"
    root.failure_category = "dispatch_timeout"
    db_session.commit()

    assert request("DELETE", f"/api/history/{root.id}").status_code == 204
    db_session.expire_all()
    persisted = db_session.get(EmailSend, root.id)
    assert persisted.status == "uncertain"
    assert persisted.failure_category == "dispatch_timeout"
    assert persisted.gmail_message_id == "gmail-root"
    assert persisted.deleted_at is not None


def test_delete_conversation_rejects_active_dispatch(db_session: Session) -> None:
    root, original = seed_conversation(db_session)
    draft = EmailDraft(
        owner_id=root.owner_id,
        purpose_id=original.purpose_id,
        conversation_id=root.id,
        recipient_name=original.recipient_name,
        recipient_email=root.recipient_email,
        attach_resume=False,
        subject="Re: Platform engineering conversation",
        body="Sending follow-up",
    )
    db_session.add(draft)
    db_session.flush()
    db_session.add(
        EmailSend(
            owner_id=root.owner_id,
            draft_id=draft.id,
            conversation_id=root.id,
            parent_send_id=root.id,
            status="sending",
            draft_fingerprint="d" * 64,
            sender_email=root.sender_email,
            recipient_email=root.recipient_email,
            subject=draft.subject,
            body=draft.body,
        )
    )
    db_session.commit()

    response = request("DELETE", f"/api/history/{root.id}")
    assert response.status_code == 409
    db_session.expire_all()
    assert db_session.get(EmailSend, root.id).deleted_at is None
    assert db_session.get(EmailDraft, original.id).deleted_at is None
