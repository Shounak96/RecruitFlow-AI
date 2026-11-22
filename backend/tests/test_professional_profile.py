from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ProfessionalProfile, ProfileProject
from app.profile_service import profile_snapshot
from tests.api_client import request


def payload(**overrides):
    data = {
        "full_name": "Jordan Lee",
        "headline": "Backend engineer",
        "email": "jordan@example.com",
        "location": "Chicago, IL",
        "summary": "Builds reliable backend systems.",
        "experience_duration": "Over three years of professional experience",
        "github_url": "https://github.com/jordan-example",
        "linkedin_url": "https://linkedin.com/in/jordan-example",
        "portfolio_url": "https://example.dev",
        "custom_links": [{"label": "Writing", "url": "https://example.dev/writing"}],
        "education": [{"degree": "M.S.", "field": "Computer Science", "institution": "Lake University", "graduation_status": "completed", "start_date": "2022-08-01", "end_date": "2024-05-01"}],
        "experiences": [{"company": "Cedar Labs", "role": "Software Engineer", "employment_type": "full_time", "start_date": "2024-06-01", "end_date": None, "is_current": True, "work_summary": "Builds Python services.", "technologies": ["Python", "PostgreSQL"]}],
        "skill_groups": [{"category": "Backend", "skills": ["Python", "FastAPI"]}],
        "projects": [{"title": "Event pipeline", "technologies": ["Kafka"], "description": "Built a fault-tolerant event pipeline and designed recovery safeguards."}],
    }
    data.update(overrides)
    return data


def test_profile_crud_persistence_and_owner_scope(db_session: Session) -> None:
    saved = request("PUT", "/api/profile", json=payload())
    assert saved.status_code == 200
    assert saved.json()["full_name"] == "Jordan Lee"
    assert saved.json()["education"][0]["graduation_status"] == "completed"
    assert set(saved.json()["projects"][0]) == {"title", "technologies", "description"}
    assert request("GET", "/api/profile").json()["projects"][0]["title"] == "Event pipeline"

    other = ProfessionalProfile(owner_id=uuid4(), full_name="Private Other Owner")
    db_session.add(other); db_session.commit()
    response = request("GET", "/api/profile").json()
    assert response["full_name"] == "Jordan Lee"
    assert "Private Other Owner" not in str(response)


def test_profile_project_maximum_ordering_and_url_validation(db_session: Session) -> None:
    projects = [
        {**payload()["projects"][0], "title": f"Project {index}"}
        for index in range(5)
    ]
    saved = request("PUT", "/api/profile", json=payload(projects=projects))
    assert saved.status_code == 200
    assert [item["title"] for item in saved.json()["projects"]] == [f"Project {i}" for i in range(5)]
    assert list(db_session.scalars(select(ProfileProject).order_by(ProfileProject.position)))[-1].position == 4

    too_many = request("PUT", "/api/profile", json=payload(projects=projects + [projects[0]]))
    assert too_many.status_code == 422
    invalid_link = request("PUT", "/api/profile", json=payload(github_url="javascript:alert(1)"))
    assert invalid_link.status_code == 422
    legacy_shape = payload(projects=[{
        "title": "Legacy shape", "technologies": [], "description": "Description",
        "role": "Developer",
    }])
    assert request("PUT", "/api/profile", json=legacy_shape).status_code == 422

    reordered = [
        {"title": "Second", "technologies": ["Python"], "description": "Edited second description."},
        {"title": "First", "technologies": ["PostgreSQL"], "description": "Edited first description."},
    ]
    assert request("PUT", "/api/profile", json=payload(projects=reordered)).status_code == 200
    reloaded = request("GET", "/api/profile").json()["projects"]
    assert reloaded == reordered
    assert request("PUT", "/api/profile", json=payload(projects=[reordered[0]])).status_code == 200
    assert request("GET", "/api/profile").json()["projects"] == [reordered[0]]


def test_profile_context_respects_explicit_education_and_duration(db_session: Session) -> None:
    ambiguous = payload(
        experience_duration=None,
        education=[{"degree": "M.S.", "field": "Computer Science", "institution": "Lake University", "graduation_status": "unspecified", "start_date": "2022-08-01", "end_date": "2024-05-01"}],
    )
    assert request("PUT", "/api/profile", json=ambiguous).status_code == 200
    snapshot = profile_snapshot(db_session, get_settings().local_owner_id, "Python APIs")
    assert snapshot["completed_education"] == []
    assert snapshot["in_progress_education"] == []
    assert snapshot["explicit_experience_duration"] is None
    selected = snapshot["jd_relevant_experiences"] + snapshot["jd_relevant_projects"]
    assert len(selected) <= 2
    assert "score" not in selected[0]["matched_evidence"]
    assert selected[0]["matched_evidence"]["matched_terms"]
