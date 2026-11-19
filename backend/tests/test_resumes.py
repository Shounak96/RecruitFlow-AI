import re
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Resume
from tests.api_client import request


PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def upload(filename: str, display_name: str = "Software resume", content: bytes = PDF_BYTES):
    return request(
        "POST",
        "/api/settings/resumes",
        data={"display_name": display_name},
        files={"file": (filename, content, "application/pdf")},
    )


def test_resume_crud_safe_storage_download_and_default_promotion(db_session: Session) -> None:
    first = upload("../../private/first.pdf", "Primary resume")
    second = upload("second.pdf", "Alternate resume")
    assert first.status_code == 201
    assert first.json()["original_filename"] == "first.pdf"
    assert first.json()["is_default"] is True
    assert second.status_code == 201
    assert second.json()["is_default"] is False

    stored = db_session.scalar(select(Resume).where(Resume.id == first.json()["id"]))
    assert stored is not None
    assert re.fullmatch(r"[0-9a-f]{32}\.pdf", stored.storage_key)
    storage_root = get_settings().resume_storage_path.resolve()
    assert (storage_root / stored.storage_key).is_file()
    assert "storage_key" not in first.json()

    downloaded = request("GET", f"/api/settings/resumes/{first.json()['id']}/download")
    assert downloaded.status_code == 200
    assert downloaded.content == PDF_BYTES
    assert downloaded.headers["content-type"] == "application/pdf"

    second_id = second.json()["id"]
    defaulted = request("PUT", f"/api/settings/resumes/{second_id}/default")
    assert defaulted.status_code == 200
    assert defaulted.json()["is_default"] is True
    assert (
        db_session.scalar(
            select(func.count()).select_from(Resume).where(Resume.is_default.is_(True))
        )
        == 1
    )

    second_row = db_session.get(Resume, second_id)
    assert second_row is not None
    second_path = storage_root / second_row.storage_key
    assert request("DELETE", f"/api/settings/resumes/{second_id}").status_code == 204
    assert not second_path.exists()
    remaining = request("GET", "/api/settings/resumes").json()
    assert len(remaining) == 1
    assert remaining[0]["is_default"] is True


def test_rejects_invalid_and_oversized_pdf() -> None:
    wrong_type = request(
        "POST",
        "/api/settings/resumes",
        data={"display_name": "Not PDF"},
        files={"file": ("resume.txt", b"hello", "text/plain")},
    )
    assert wrong_type.status_code == 415

    bad_content = upload("fake.pdf", content=b"This is not a PDF")
    assert bad_content.status_code == 422

    oversized = upload(
        "large.pdf",
        content=b"%PDF-1.4\n" + (b"x" * (5 * 1024 * 1024)) + b"%%EOF",
    )
    assert oversized.status_code == 413


def test_owner_isolation_and_invalid_storage_reference(db_session: Session) -> None:
    other = Resume(
        owner_id=uuid4(),
        display_name="Other owner resume",
        original_filename="other.pdf",
        storage_key=f"{uuid4().hex}.pdf",
        size_bytes=len(PDF_BYTES),
        is_default=True,
    )
    db_session.add(other)
    db_session.commit()

    assert request("GET", "/api/settings/resumes").json() == []
    assert request("GET", f"/api/settings/resumes/{other.id}/download").status_code == 404
    assert request("DELETE", f"/api/settings/resumes/{other.id}").status_code == 404


def test_delete_removes_resume_and_file(db_session: Session) -> None:
    created = upload("delete-me.pdf")
    resume_id = created.json()["id"]
    stored = db_session.get(Resume, resume_id)
    assert stored is not None
    path = get_settings().resume_storage_path.resolve() / stored.storage_key
    assert path.exists()

    assert request("DELETE", f"/api/settings/resumes/{resume_id}").status_code == 204
    assert not path.exists()
    assert request("GET", f"/api/settings/resumes/{resume_id}/download").status_code == 404

