from pathlib import Path

import pytest
from pypdf import PdfWriter

from app import resume_text
from app.resume_text import MAX_RESUME_TEXT_CHARS, ResumeExtractionError, extract_resume_text


def test_image_only_or_empty_pdf_is_handled(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with path.open("wb") as output:
        writer.write(output)
    with pytest.raises(ResumeExtractionError, match="no extractable text"):
        extract_resume_text(path)


def test_resume_text_is_bounded(monkeypatch, tmp_path: Path) -> None:
    class Page:
        def extract_text(self):
            return "x" * (MAX_RESUME_TEXT_CHARS + 100)

    class Reader:
        pages = [Page()]

    monkeypatch.setattr(resume_text, "PdfReader", lambda path: Reader())
    assert len(extract_resume_text(tmp_path / "resume.pdf")) == MAX_RESUME_TEXT_CHARS


def test_corrupt_pdf_has_safe_extraction_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"not a pdf")
    with pytest.raises(ResumeExtractionError, match="could not be extracted"):
        extract_resume_text(path)

