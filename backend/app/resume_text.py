from pathlib import Path

from pypdf import PdfReader

MAX_RESUME_TEXT_CHARS = 12_000
MAX_RESUME_PAGES = 25


class ResumeExtractionError(Exception):
    pass


def extract_resume_text(path: Path) -> str:
    try:
        reader = PdfReader(path)
        parts: list[str] = []
        length = 0
        for page in reader.pages[:MAX_RESUME_PAGES]:
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            remaining = MAX_RESUME_TEXT_CHARS - length
            if remaining <= 0:
                break
            chunk = text[:remaining]
            parts.append(chunk)
            length += len(chunk)
    except Exception as exc:
        raise ResumeExtractionError("Resume text could not be extracted.") from exc
    result = "\n\n".join(parts).strip()
    if not result:
        raise ResumeExtractionError(
            "This PDF has no extractable text. Use a text-based PDF or choose No resume."
        )
    return result[:MAX_RESUME_TEXT_CHARS]

