import base64
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.gmail_replies import clean_received_body
from app.gmail_service import (
    GMAIL_READONLY_SCOPE,
    GMAIL_SCOPES,
    GmailService,
    GmailUpstreamError,
    get_gmail_service,
)
from app.main import app
from app.models import (
    EmailDraft,
    EmailPurpose,
    EmailSend,
    GmailConnection,
    ReceivedGmailMessage,
    SenderProfile,
)
from tests.api_client import request


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "Yes, I would be interested.\n\nOn Thu, Nov 19, 2026 at 10:30 AM Jordan <j@example.com> wrote:\n> Hello Taylor,\n> Original message",
            "Yes, I would be interested.",
        ),
        (
            "Yes, I would be interested.\n\nOn Thu, Nov 19, 2026 at 10:30 AM Jordan\n<j@example.com> wrote:\n> Hello Taylor,\n> Original message",
            "Yes, I would be interested.",
        ),
        ("Short plain reply.", "Short plain reply."),
        ("The expression 3 > 2 is valid.\nPlease keep it.", "The expression 3 > 2 is valid.\nPlease keep it."),
        ("Please compare:\n> one option", "Please compare:\n> one option"),
        (
            "Thanks for reaching out.\n\n-----Original Message-----\nFrom: Jordan\nOld content",
            "Thanks for reaching out.",
        ),
        ("> Entire message is quoted\n> Preserve when no reply text exists", "> Entire message is quoted\n> Preserve when no reply text exists"),
    ],
)
def test_clean_received_body_is_conservative(body: str, expected: str) -> None:
    assert clean_received_body(body) == expected


def gmail_message(
    message_id: str,
    *,
    thread_id: str,
    sender: str = "Taylor Recruiter <taylor@example.com>",
    recipient: str = "verified@example.com",
    body: str = "Thanks for reaching out.\n\nLet's talk next week.",
    mime_type: str = "text/plain",
) -> dict:
    payload: dict = {
        "mimeType": mime_type,
        "headers": [
            {"name": "From", "value": sender},
            {"name": "To", "value": recipient},
            {"name": "Subject", "value": "Re: Platform engineering conversation"},
            {"name": "Message-ID", "value": f"<{message_id}@example.com>"},
        ],
        "body": {"data": _encoded(body)},
    }
    return {
        "id": message_id,
        "threadId": thread_id,
        "internalDate": str(
            int((datetime.now(timezone.utc) + timedelta(minutes=1)).timestamp() * 1000)
        ),
        "payload": payload,
    }


class ReadOnlyGmailService:
    def __init__(self, threads: dict[str, dict | Exception]) -> None:
        self.threads = threads
        self.read_calls: list[str] = []

    def decrypt_token(self, value: str) -> str:
        del value
        return "access-token"

    async def get_thread(self, access_token: str, thread_id: str) -> dict:
        assert access_token == "access-token"
        self.read_calls.append(thread_id)
        result = self.threads[thread_id]
        if isinstance(result, Exception):
            raise result
        return result


def seed_connection(db: Session, *, read_scope: bool = True) -> GmailConnection:
    scopes = list(GMAIL_SCOPES)
    if not read_scope:
        scopes.remove(GMAIL_READONLY_SCOPE)
    connection = GmailConnection(
        owner_id=get_settings().local_owner_id,
        google_subject="google-subject",
        email="verified@example.com",
        encrypted_access_token="encrypted-access",
        encrypted_refresh_token="encrypted-refresh",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=" ".join(scopes),
        is_valid=True,
    )
    db.add(connection)
    db.commit()
    return connection


def seed_conversation(db: Session, *, suffix: str = "root") -> EmailSend:
    owner = get_settings().local_owner_id
    profile = db.scalar(select(SenderProfile).where(SenderProfile.owner_id == owner))
    if profile is None:
        db.add(SenderProfile(owner_id=owner, name="Jordan"))
    purpose = db.scalar(select(EmailPurpose).where(EmailPurpose.owner_id == owner))
    if purpose is None:
        purpose = EmailPurpose(owner_id=owner, title="Introduction", description="Connect")
        db.add(purpose)
        db.flush()
    draft = EmailDraft(
        owner_id=owner,
        purpose_id=purpose.id,
        recipient_name="Taylor Recruiter",
        recipient_email="taylor@example.com",
        company="Example Co",
        attach_resume=False,
        subject="Platform engineering conversation",
        body="Hi Taylor,\n\nCould we talk?\n\nBest,\nJordan",
        sent_at=datetime.now(timezone.utc),
    )
    db.add(draft)
    db.flush()
    send = EmailSend(
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
        gmail_message_id=f"gmail-{suffix}",
        gmail_thread_id=f"thread-{suffix}",
        rfc_message_id=f"<{suffix}@verified.example>",
    )
    db.add(send)
    db.commit()
    return send


def override_service(service: ReadOnlyGmailService) -> None:
    app.dependency_overrides[get_gmail_service] = lambda: service


def test_sync_persists_only_matching_replies_and_is_idempotent(db_session: Session) -> None:
    seed_connection(db_session)
    root = seed_conversation(db_session)
    messages = [
        gmail_message("gmail-root", thread_id="thread-root"),
        gmail_message("reply-1", thread_id="thread-root"),
        gmail_message("owner-copy", thread_id="thread-root", sender="verified@example.com"),
        gmail_message("unrelated", thread_id="thread-root", sender="Other <other@example.com>"),
        gmail_message("wrong-recipient", thread_id="thread-root", recipient="someone@example.com"),
    ]
    group_message = gmail_message("group-reply", thread_id="thread-root")
    group_message["payload"]["headers"].append(
        {"name": "Cc", "value": "Other Participant <other@example.com>"}
    )
    messages.append(group_message)
    service = ReadOnlyGmailService({"thread-root": {"id": "thread-root", "messages": messages}})
    override_service(service)

    first = request("POST", "/api/gmail/replies/sync")
    assert first.status_code == 200
    assert first.json() == {
        "threads_checked": 1,
        "threads_failed": 0,
        "new_messages": 1,
        "partial_failure": False,
    }
    assert request("POST", "/api/gmail/replies/sync").json()["new_messages"] == 0
    rows = list(db_session.scalars(select(ReceivedGmailMessage)))
    assert len(rows) == 1
    assert rows[0].conversation_id == root.id
    assert rows[0].body == "Thanks for reaching out.\n\nLet's talk next week."
    assert service.read_calls == ["thread-root", "thread-root"]


def test_history_merges_replies_and_tracks_local_unread_state(db_session: Session) -> None:
    seed_connection(db_session)
    root = seed_conversation(db_session)
    service = ReadOnlyGmailService({
        "thread-root": {
            "id": "thread-root",
            "messages": [gmail_message("reply-1", thread_id="thread-root")],
        }
    })
    override_service(service)
    request("POST", "/api/gmail/replies/sync")

    assert request("GET", "/api/history").json()[0]["unread_reply_count"] == 1
    detail = request("GET", f"/api/history/{root.id}").json()
    assert [message["direction"] for message in detail["messages"]] == ["outgoing", "incoming"]
    assert detail["messages"][1]["delivery_state"] is None
    assert request("POST", f"/api/history/{root.id}/received/read").status_code == 204
    assert request("GET", "/api/history").json()[0]["unread_reply_count"] == 0


def test_history_cleans_existing_persisted_reply_but_preserves_full_body(
    db_session: Session,
) -> None:
    seed_connection(db_session)
    root = seed_conversation(db_session)
    original = (
        "Yes, I would be interested.\n\n"
        "On Thu, Nov 19, 2026 at 10:30 AM Jordan <j@example.com> wrote:\n"
        "> Hello Taylor,\n> Would you be interested?"
    )
    message = ReceivedGmailMessage(
        owner_id=get_settings().local_owner_id,
        conversation_id=root.id,
        gmail_message_id="persisted-before-cleanup",
        gmail_thread_id=root.gmail_thread_id,
        sender_name="Taylor Recruiter",
        sender_email=root.recipient_email,
        recipient_email=root.sender_email,
        subject=f"Re: {root.subject}",
        body=original,
        received_at=datetime.now(timezone.utc),
        rfc_message_id="<persisted-before-cleanup@example.com>",
    )
    db_session.add(message)
    db_session.commit()

    incoming = request("GET", f"/api/history/{root.id}").json()["messages"][-1]
    assert incoming["body"] == "Yes, I would be interested."
    assert incoming["full_body"] == original
    db_session.expire_all()
    assert db_session.get(ReceivedGmailMessage, message.id).body == original


def test_html_only_and_malformed_mime_are_stored_as_safe_plain_text(db_session: Session) -> None:
    seed_connection(db_session)
    seed_conversation(db_session)
    html = gmail_message(
        "reply-html",
        thread_id="thread-root",
        mime_type="text/html",
        body="<p>Hello <b>Jordan</b>,</p><script>alert(1)</script><p>Let's talk.</p>",
    )
    malformed = gmail_message("reply-empty", thread_id="thread-root")
    malformed["payload"]["body"]["data"] = "%%%not-base64%%%"
    alternative = gmail_message("reply-alternative", thread_id="thread-root")
    alternative["payload"]["mimeType"] = "multipart/alternative"
    alternative["payload"]["body"] = {}
    alternative["payload"]["parts"] = [
        {"mimeType": "text/plain", "filename": "", "headers": [], "body": {"data": _encoded("Preferred plain reply")}},
        {"mimeType": "text/html", "filename": "", "headers": [], "body": {"data": _encoded("<p>HTML fallback</p>")}},
    ]
    service = ReadOnlyGmailService({
        "thread-root": {"id": "thread-root", "messages": [html, malformed, alternative]}
    })
    override_service(service)
    result = request("POST", "/api/gmail/replies/sync").json()
    assert result["new_messages"] == 3
    rows = {row.gmail_message_id: row for row in db_session.scalars(select(ReceivedGmailMessage))}
    assert "Hello Jordan," in rows["reply-html"].body
    assert "alert(1)" not in rows["reply-html"].body
    assert "<p>" not in rows["reply-html"].body
    assert rows["reply-empty"].body == ""
    assert rows["reply-alternative"].body == "Preferred plain reply"


def test_missing_read_scope_requires_reauthorization_without_read_call(db_session: Session) -> None:
    seed_connection(db_session, read_scope=False)
    seed_conversation(db_session)
    service = ReadOnlyGmailService({})
    override_service(service)
    status = request("GET", "/api/gmail/status").json()
    assert status["connected"] is True
    assert status["read_authorized"] is False
    assert status["reauthorization_required"] is True
    response = request("POST", "/api/gmail/replies/sync")
    assert response.status_code == 409
    assert "Reconnect Gmail" in response.json()["detail"]
    assert service.read_calls == []


def test_multiple_threads_continue_after_timeout(db_session: Session) -> None:
    seed_connection(db_session)
    seed_conversation(db_session, suffix="one")
    seed_conversation(db_session, suffix="two")
    service = ReadOnlyGmailService({
        "thread-one": GmailUpstreamError("timeout", category="thread_timeout"),
        "thread-two": {
            "id": "thread-two",
            "messages": [gmail_message("reply-two", thread_id="thread-two")],
        },
    })
    override_service(service)
    result = request("POST", "/api/gmail/replies/sync").json()
    assert result["threads_checked"] == 2
    assert result["threads_failed"] == 1
    assert result["new_messages"] == 1
    assert result["partial_failure"] is True


def test_deleted_conversation_is_never_fetched_or_resurrected(db_session: Session) -> None:
    seed_connection(db_session)
    root = seed_conversation(db_session)
    assert request("DELETE", f"/api/history/{root.id}").status_code == 204
    service = ReadOnlyGmailService({})
    override_service(service)
    result = request("POST", "/api/gmail/replies/sync").json()
    assert result["threads_checked"] == 0
    assert service.read_calls == []
    assert db_session.scalar(select(func.count(ReceivedGmailMessage.id))) == 0


def test_thread_get_is_read_only_and_bounded_to_full_format() -> None:
    seen = []

    async def handler(request_value: httpx.Request) -> httpx.Response:
        seen.append(request_value)
        return httpx.Response(200, json={"id": "thread/id", "messages": []})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await GmailService(client).get_thread("token", "thread/id")
            assert result["id"] == "thread/id"

    import asyncio
    asyncio.run(run())
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.raw_path.endswith(b"/threads/thread%2Fid?format=full")
    assert seen[0].url.params.get("format") == "full"
