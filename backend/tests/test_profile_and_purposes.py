from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import EmailPurpose, SenderProfile
from tests.api_client import request


def test_profile_is_server_managed_and_persists(db_session: Session) -> None:
    initial = request("GET", "/api/settings/profile")
    assert initial.status_code == 200
    assert initial.json() == {
        "name": "Local user",
        "gmail_connected": False,
        "gmail_email": None,
    }

    rejected_owner = request(
        "PUT",
        "/api/settings/profile",
        json={"name": "  Jordan Recruiter  ", "owner_id": str(uuid4())},
    )
    assert rejected_owner.status_code == 422

    updated = request(
        "PUT",
        "/api/settings/profile",
        json={"name": "  Jordan Recruiter  "},
    )
    assert updated.status_code == 200
    assert updated.json() == {
        "name": "Jordan Recruiter",
        "gmail_connected": False,
        "gmail_email": None,
    }

    profile = db_session.scalar(select(SenderProfile))
    assert profile is not None
    assert profile.owner_id == get_settings().local_owner_id
    assert profile.name == "Jordan Recruiter"


def test_purpose_crud_defaults_and_default_deletion(db_session: Session) -> None:
    first = request(
        "POST",
        "/api/settings/purposes",
        json={"title": "Introduction", "description": "Introduce myself to a recruiter."},
    )
    second = request(
        "POST",
        "/api/settings/purposes",
        json={"title": "Follow-up", "description": "Follow up on an application."},
    )
    assert first.status_code == 201
    assert first.json()["is_default"] is True
    assert second.status_code == 201
    assert second.json()["is_default"] is False

    purpose_id = second.json()["id"]
    updated = request(
        "PUT",
        f"/api/settings/purposes/{purpose_id}",
        json={"title": "Application follow-up", "description": "A concise follow-up note."},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Application follow-up"

    made_default = request("PUT", f"/api/settings/purposes/{purpose_id}/default")
    assert made_default.status_code == 200
    assert made_default.json()["is_default"] is True
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(EmailPurpose)
            .where(EmailPurpose.is_default.is_(True))
        )
        == 1
    )

    deleted = request("DELETE", f"/api/settings/purposes/{purpose_id}")
    assert deleted.status_code == 204
    purposes = request("GET", "/api/settings/purposes").json()
    assert len(purposes) == 1
    assert purposes[0]["is_default"] is True


def test_owner_isolation_hides_other_owners_data(db_session: Session) -> None:
    other = EmailPurpose(
        owner_id=uuid4(),
        title="Private other-owner purpose",
        description="Must not be returned.",
        is_default=True,
    )
    db_session.add(other)
    db_session.commit()

    listed = request("GET", "/api/settings/purposes")
    assert listed.status_code == 200
    assert listed.json() == []
    assert request("DELETE", f"/api/settings/purposes/{other.id}").status_code == 404
    assert db_session.get(EmailPurpose, other.id) is not None


def test_postgresql_enforces_one_default_purpose_per_owner(db_session: Session) -> None:
    owner_id = get_settings().local_owner_id
    db_session.add_all(
        [
            EmailPurpose(owner_id=owner_id, title="One", description="First", is_default=True),
            EmailPurpose(owner_id=owner_id, title="Two", description="Second", is_default=True),
        ]
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_purpose_validation_and_deletion() -> None:
    invalid = request(
        "POST", "/api/settings/purposes", json={"title": " ", "description": " "}
    )
    assert invalid.status_code == 422

    created = request(
        "POST",
        "/api/settings/purposes",
        json={"title": "Networking", "description": "Ask for an informational chat."},
    )
    purpose_id = created.json()["id"]
    assert request("DELETE", f"/api/settings/purposes/{purpose_id}").status_code == 204
    assert request("DELETE", f"/api/settings/purposes/{purpose_id}").status_code == 404

