from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.automatic_sending import (
    AutomaticSendingError,
    claim_automatic_send,
    validate_automatic_authorization,
)
from app.config import get_settings
from app.gmail_service import GMAIL_SCOPES, get_gmail_service
from app.main import app
from app.models import (
    AutomaticSendAuthorization,
    AutomaticSendClaim,
    EmailDraft,
    EmailPurpose,
    EmailSend,
    GmailConnection,
    ReceivedGmailMessage,
    SenderProfile,
    SendingPreference,
)
from tests.api_client import request
from tests.test_history_followups import seed_conversation


class NoSendGmailService:
    def __init__(self) -> None:
        self.send_calls = []

    async def send_message(self, *args, **kwargs):
        self.send_calls.append((args, kwargs))
        raise AssertionError("Stage A must not call Gmail send")


def seed_draft(db: Session, *, recipient: str = "taylor@example.com") -> EmailDraft:
    owner = get_settings().local_owner_id
    profile = SenderProfile(owner_id=owner, name="Jordan")
    purpose = EmailPurpose(
        owner_id=owner,
        title="Introduction",
        description="Introduce the sender.",
        is_default=True,
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
    db.add_all([profile, purpose, connection])
    db.flush()
    draft = EmailDraft(
        owner_id=owner,
        purpose_id=purpose.id,
        recipient_name="Taylor",
        recipient_email=recipient,
        attach_resume=False,
        subject="Platform role",
        body="Hi Taylor,\n\nWould you be open to connecting?\n\nBest,\nJordan",
    )
    db.add(draft)
    db.commit()
    return draft


def enable_automation(
    *, initial: bool = True, followups: bool = False, limit: int = 1
):
    return request(
        "PUT",
        "/api/settings/sending-preferences",
        json={
            "sending_mode": "automatic",
            "automatic_initial_outreach": initial,
            "automatic_followups": followups,
            "daily_send_limit": limit,
            "confirm_automatic_sending": True,
        },
    )


def authorize(draft: EmailDraft, *, recipient: str | None = None):
    return request(
        "POST",
        "/api/settings/automatic-authorizations",
        json={
            "draft_id": str(draft.id),
            "recipient_email": recipient or draft.recipient_email,
            "approved": True,
        },
    )


def update_payload(draft: EmailDraft, **changes) -> dict:
    payload = {
        "recipient_name": draft.recipient_name,
        "recipient_email": draft.recipient_email,
        "company": draft.company,
        "role": draft.role,
        "job_description": draft.job_description,
        "purpose_id": str(draft.purpose_id),
        "resume_id": None,
        "attach_resume": False,
        "subject": draft.subject,
        "body": draft.body,
    }
    payload.update(changes)
    return payload


def test_preferences_default_manual_persist_and_require_explicit_opt_in(
    db_session: Session,
) -> None:
    first = request("GET", "/api/settings/sending-preferences")
    assert first.status_code == 200
    assert first.json() == {
        "sending_mode": "manual",
        "automatic_initial_outreach": False,
        "automatic_followups": False,
        "daily_send_limit": 5,
        "automation_consented_at": None,
        "automation_disabled_at": None,
        "stage_a_dispatch_enabled": False,
    }
    rejected = request(
        "PUT",
        "/api/settings/sending-preferences",
        json={
            "sending_mode": "automatic",
            "automatic_initial_outreach": True,
            "automatic_followups": False,
            "daily_send_limit": 3,
            "confirm_automatic_sending": False,
        },
    )
    assert rejected.status_code == 422
    enabled = enable_automation(limit=3)
    assert enabled.status_code == 200
    assert enabled.json()["sending_mode"] == "automatic"
    assert enabled.json()["automation_consented_at"] is not None
    assert request("GET", "/api/settings/sending-preferences").json()["daily_send_limit"] == 3
    db_session.expire_all()
    assert db_session.scalar(select(SendingPreference)).sending_mode == "automatic"


@pytest.mark.parametrize("limit", [11, 50, 500])
def test_custom_daily_limits_are_valid_and_persisted(
    db_session: Session, limit: int
) -> None:
    enabled = enable_automation(limit=limit)
    assert enabled.status_code == 200
    assert enabled.json()["daily_send_limit"] == limit
    db_session.expire_all()
    assert db_session.scalar(select(SendingPreference)).daily_send_limit == limit
    assert request("GET", "/api/settings/sending-preferences").json()[
        "daily_send_limit"
    ] == limit


@pytest.mark.parametrize("limit", [0, -1, 500.1, 501, "11", "not-a-number"])
def test_custom_daily_limits_reject_invalid_values(limit) -> None:
    response = request(
        "PUT",
        "/api/settings/sending-preferences",
        json={
            "sending_mode": "automatic",
            "automatic_initial_outreach": True,
            "automatic_followups": False,
            "daily_send_limit": limit,
            "confirm_automatic_sending": True,
        },
    )
    assert response.status_code == 422
    assert "daily_send_limit" in response.text


def test_authorization_is_draft_recipient_owner_and_gmail_identity_scoped(
    db_session: Session,
) -> None:
    draft = seed_draft(db_session)
    service = NoSendGmailService()
    app.dependency_overrides[get_gmail_service] = lambda: service
    assert authorize(draft).status_code == 409
    assert enable_automation().status_code == 200
    assert authorize(draft, recipient="other@example.com").status_code == 409
    created = authorize(draft)
    assert created.status_code == 201
    data = created.json()
    assert data["draft_id"] == str(draft.id)
    assert data["recipient_email"] == draft.recipient_email
    assert data["sender_email"] == "verified@example.com"
    assert data["authorization_kind"] == "initial"
    assert data["revoked_at"] is None
    assert authorize(draft).status_code == 409
    db_session.expire_all()
    authorization = db_session.get(AutomaticSendAuthorization, data["id"])
    validated, validated_draft, _ = validate_automatic_authorization(
        db_session, get_settings().local_owner_id, authorization.id
    )
    assert validated.id == authorization.id and validated_draft.id == draft.id
    assert service.send_calls == []


def test_draft_edit_and_delete_revoke_authorization_and_tombstone_blocks_use(
    db_session: Session,
) -> None:
    draft = seed_draft(db_session)
    enable_automation()
    authorization_id = authorize(draft).json()["id"]
    edited = request(
        "PUT",
        f"/api/drafts/{draft.id}",
        json=update_payload(draft, body=draft.body + "\n\nUpdated."),
    )
    assert edited.status_code == 200
    db_session.expire_all()
    authorization = db_session.get(AutomaticSendAuthorization, authorization_id)
    assert authorization.revocation_reason == "draft_changed"
    with pytest.raises(AutomaticSendingError):
        validate_automatic_authorization(db_session, get_settings().local_owner_id, authorization.id)

    replacement = authorize(db_session.get(EmailDraft, draft.id))
    assert replacement.status_code == 201
    replacement_id = replacement.json()["id"]
    assert request("DELETE", f"/api/drafts/{draft.id}").status_code == 204
    db_session.expire_all()
    assert db_session.get(EmailDraft, draft.id).deleted_at is not None
    assert db_session.get(AutomaticSendAuthorization, replacement_id).revocation_reason == "draft_deleted"


def test_revocation_disable_identity_change_and_prior_send_are_rejected(
    db_session: Session,
) -> None:
    draft = seed_draft(db_session)
    enable_automation()
    authorization_id = authorize(draft).json()["id"]
    assert request("DELETE", f"/api/settings/automatic-authorizations/{authorization_id}").status_code == 204
    assert request("DELETE", f"/api/settings/automatic-authorizations/{authorization_id}").status_code == 204
    db_session.expire_all()
    authorization = db_session.get(AutomaticSendAuthorization, authorization_id)
    assert authorization.revocation_reason == "user_revoked"

    second_id = authorize(draft).json()["id"]
    connection = db_session.scalar(select(GmailConnection))
    connection.email = "changed@example.com"
    db_session.commit()
    with pytest.raises(AutomaticSendingError, match="Gmail identity changed"):
        validate_automatic_authorization(db_session, get_settings().local_owner_id, second_id)
    connection.email = "verified@example.com"
    db_session.commit()
    send = EmailSend(
        owner_id=get_settings().local_owner_id,
        draft_id=draft.id,
        status="uncertain",
        draft_fingerprint="a" * 64,
        sender_email=connection.email,
        recipient_email=draft.recipient_email,
        subject=draft.subject,
        body=draft.body,
    )
    db_session.add(send)
    db_session.commit()
    with pytest.raises(AutomaticSendingError, match="already has a send attempt"):
        validate_automatic_authorization(db_session, get_settings().local_owner_id, second_id)

    disabled = request("POST", "/api/settings/sending-preferences/disable")
    assert disabled.status_code == 200
    assert disabled.json()["sending_mode"] == "manual"
    db_session.expire_all()
    assert db_session.get(AutomaticSendAuthorization, second_id).revocation_reason == "automation_disabled"


def test_utc_daily_claim_limit_duplicate_guard_and_uncertain_counting(
    db_session: Session,
) -> None:
    first = seed_draft(db_session, recipient="first@example.com")
    owner = get_settings().local_owner_id
    purpose = db_session.get(EmailPurpose, first.purpose_id)
    second = EmailDraft(
        owner_id=owner,
        purpose_id=purpose.id,
        recipient_name="Second",
        recipient_email="second@example.com",
        attach_resume=False,
        subject="Second",
        body="Hello Second,\n\nChecking in.\n\nBest,\nJordan",
    )
    db_session.add(second)
    db_session.commit()
    enable_automation(limit=1)
    first_authorization = authorize(first).json()["id"]
    second_authorization = authorize(second).json()["id"]
    claim_time = datetime(2026, 11, 20, 1, 0, tzinfo=timezone(timedelta(hours=5)))
    claim = claim_automatic_send(
        db_session, owner, first_authorization, now=claim_time
    )
    db_session.commit()
    assert claim.claim_date.isoformat() == "2026-11-19"
    assert request("POST", f"/api/gmail/reviews/{first.id}").status_code == 409
    with pytest.raises(AutomaticSendingError, match="already has a send claim"):
        claim_automatic_send(db_session, owner, first_authorization, now=claim_time)
    db_session.rollback()
    claim.status = "uncertain"
    db_session.commit()
    with pytest.raises(AutomaticSendingError, match="daily limit"):
        claim_automatic_send(db_session, owner, second_authorization, now=claim_time)
    db_session.rollback()
    assert db_session.scalar(select(AutomaticSendClaim)).status == "uncertain"


def test_manual_review_still_works_and_revokes_unused_automatic_authorization(
    db_session: Session,
) -> None:
    draft = seed_draft(db_session)
    enable_automation()
    authorization_id = authorize(draft).json()["id"]
    service = NoSendGmailService()
    app.dependency_overrides[get_gmail_service] = lambda: service
    review = request("POST", f"/api/gmail/reviews/{draft.id}")
    assert review.status_code == 201
    assert review.json()["status"] == "pending"
    assert service.send_calls == []
    db_session.expire_all()
    authorization = db_session.get(AutomaticSendAuthorization, authorization_id)
    assert authorization.revocation_reason == "manual_review_created"


def test_followup_category_tombstone_and_received_reply_exclusion(
    db_session: Session,
) -> None:
    root, original = seed_conversation(db_session)
    owner = get_settings().local_owner_id
    followup = EmailDraft(
        owner_id=owner,
        purpose_id=original.purpose_id,
        conversation_id=root.id,
        recipient_name=original.recipient_name,
        recipient_email=root.recipient_email,
        attach_resume=False,
        subject=f"Re: {root.subject}",
        body="Hi Taylor,\n\nFollowing up.\n\nBest,\nJordan",
    )
    received = ReceivedGmailMessage(
        owner_id=owner,
        conversation_id=root.id,
        gmail_message_id="received-stage-a",
        gmail_thread_id=root.gmail_thread_id,
        sender_name="Taylor",
        sender_email=root.recipient_email,
        recipient_email=root.sender_email,
        subject=f"Re: {root.subject}",
        body="Interested.",
        received_at=datetime.now(timezone.utc),
        rfc_message_id="<received-stage-a@example.com>",
    )
    db_session.add_all([followup, received])
    db_session.flush()
    reply = EmailDraft(
        owner_id=owner,
        purpose_id=original.purpose_id,
        conversation_id=root.id,
        reply_to_received_message_id=received.id,
        recipient_name="Taylor",
        recipient_email=root.recipient_email,
        attach_resume=False,
        subject=f"Re: {root.subject}",
        body="Hi Taylor,\n\nThanks for your reply.\n\nBest,\nJordan",
    )
    db_session.add(reply)
    db_session.commit()

    enable_automation(initial=True, followups=False)
    assert authorize(followup).status_code == 409
    enabled = request(
        "PUT",
        "/api/settings/sending-preferences",
        json={
            "sending_mode": "automatic",
            "automatic_initial_outreach": False,
            "automatic_followups": True,
            "daily_send_limit": 1,
            "confirm_automatic_sending": False,
        },
    )
    assert enabled.status_code == 200
    followup_authorization = authorize(followup)
    assert followup_authorization.status_code == 201
    assert followup_authorization.json()["authorization_kind"] == "follow_up"
    reply_authorization = authorize(reply)
    assert reply_authorization.status_code == 409
    assert "received messages" in reply_authorization.json()["detail"]

    assert request("DELETE", f"/api/history/{root.id}").status_code == 204
    db_session.expire_all()
    authorization = db_session.get(
        AutomaticSendAuthorization, followup_authorization.json()["id"]
    )
    assert authorization.revocation_reason == "conversation_deleted"


def test_authorizations_are_owner_scoped_and_stage_a_has_no_dispatch_route(
    db_session: Session,
) -> None:
    draft = seed_draft(db_session)
    enable_automation()
    authorization_id = authorize(draft).json()["id"]
    other = db_session.get(AutomaticSendAuthorization, authorization_id)
    other.owner_id = uuid4()
    db_session.commit()
    assert request("GET", "/api/settings/automatic-authorizations").json() == []
    assert request("DELETE", f"/api/settings/automatic-authorizations/{authorization_id}").status_code == 404
    assert "/api/settings/automatic-authorizations/dispatch" not in app.openapi()["paths"]
