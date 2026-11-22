import base64
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any


MAX_RECEIVED_BODY_CHARS = 20_000
MAX_ENCODED_PART_CHARS = 400_000
MAX_MIME_PARTS = 100
MAX_MIME_DEPTH = 10
ORIGINAL_MESSAGE_SEPARATOR = re.compile(
    r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.IGNORECASE
)
GMAIL_WROTE_MARKER = re.compile(r"^On\s+.+\s+wrote:\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedGmailMessage:
    gmail_message_id: str
    gmail_thread_id: str
    sender_name: str | None
    sender_email: str
    recipient_emails: frozenset[str]
    subject: str | None
    body: str
    received_at: datetime
    rfc_message_id: str | None
    in_reply_to: str | None


class _PlainTextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "head"}:
            self._ignored += 1
        elif not self._ignored and tag in {"br", "p", "div", "li", "tr", "blockquote"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head"} and self._ignored:
            self._ignored -= 1
        elif not self._ignored and tag in {"p", "div", "li", "tr", "blockquote"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored:
            self.parts.append(data)


def _headers(payload: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    raw_headers = payload.get("headers", [])
    if not isinstance(raw_headers, list):
        return result
    for item in raw_headers[:200]:
        if not isinstance(item, dict):
            continue
        name, value = item.get("name"), item.get("value")
        if isinstance(name, str) and isinstance(value, str):
            result[name.lower()] = value
    return result


def _decoded_header(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    try:
        value = str(make_header(decode_header(value)))
    except (LookupError, UnicodeError):
        pass
    value = " ".join(value.replace("\r", " ").replace("\n", " ").split()).strip()
    return value[:limit] or None


def _part_bytes(part: dict[str, Any]) -> bytes:
    body = part.get("body")
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, str) or len(data) > MAX_ENCODED_PART_CHARS:
        return b""
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (ValueError, TypeError):
        return b""


def _decode_part(part: dict[str, Any]) -> str:
    raw = _part_bytes(part)
    if not raw:
        return ""
    charset = "utf-8"
    content_type = _headers(part).get("content-type", "")
    match = re.search(r"charset\s*=\s*[\"']?([^;\"'\s]+)", content_type, re.I)
    if match:
        charset = match.group(1)
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _body_candidates(
    part: dict[str, Any], *, depth: int = 0, remaining: list[int] | None = None
) -> tuple[list[str], list[str]]:
    if remaining is None:
        remaining = [MAX_MIME_PARTS]
    if depth > MAX_MIME_DEPTH or remaining[0] <= 0:
        return [], []
    remaining[0] -= 1
    filename = part.get("filename")
    headers = _headers(part)
    disposition = headers.get("content-disposition", "").lower()
    if (isinstance(filename, str) and filename.strip()) or "attachment" in disposition:
        return [], []
    mime_type = str(part.get("mimeType") or "").lower()
    plain: list[str] = []
    html: list[str] = []
    if mime_type == "text/plain":
        value = _decode_part(part)
        if value:
            plain.append(value)
    elif mime_type == "text/html":
        value = _decode_part(part)
        if value:
            html.append(value)
    parts = part.get("parts")
    if isinstance(parts, list):
        for child in parts:
            if isinstance(child, dict):
                child_plain, child_html = _body_candidates(
                    child, depth=depth + 1, remaining=remaining
                )
                plain.extend(child_plain)
                html.extend(child_html)
    return plain, html


def _normalize_plain_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = [re.sub(r"[\t ]+", " ", line).strip() for line in value.split("\n")]
    result: list[str] = []
    for line in lines:
        if line or (result and result[-1]):
            result.append(line)
    return "\n".join(result).strip()[:MAX_RECEIVED_BODY_CHARS]


def clean_received_body(value: str) -> str:
    """Return a conservative read-time view without a recognizable quoted chain."""
    normalized = _normalize_plain_text(value)
    if not normalized:
        return normalized
    lines = normalized.split("\n")

    def useful_prefix(index: int) -> bool:
        return any(line.strip() for line in lines[:index])

    def first_nonblank(index: int) -> str | None:
        return next((line for line in lines[index:] if line.strip()), None)

    for index, line in enumerate(lines):
        if useful_prefix(index) and ORIGINAL_MESSAGE_SEPARATOR.fullmatch(line):
            cleaned = _normalize_plain_text("\n".join(lines[:index]))
            return cleaned or normalized

    for start in range(len(lines)):
        if not useful_prefix(start) or not lines[start].lstrip().lower().startswith("on "):
            continue
        for end in range(start, min(start + 3, len(lines))):
            marker = " ".join(line.strip() for line in lines[start : end + 1])
            following = first_nonblank(end + 1)
            if GMAIL_WROTE_MARKER.fullmatch(marker) and following and following.lstrip().startswith(">"):
                cleaned = _normalize_plain_text("\n".join(lines[:start]))
                return cleaned or normalized

    for index, line in enumerate(lines):
        if not useful_prefix(index) or not line.lstrip().startswith(">"):
            continue
        quoted_tail = [item for item in lines[index:] if item.strip()]
        if len(quoted_tail) >= 2 and all(item.lstrip().startswith(">") for item in quoted_tail):
            cleaned = _normalize_plain_text("\n".join(lines[:index]))
            return cleaned or normalized
    return normalized


def _html_to_text(value: str) -> str:
    parser = _PlainTextHTMLParser()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        return ""
    return _normalize_plain_text("".join(parser.parts))


def parse_gmail_message(message: dict[str, Any]) -> ParsedGmailMessage | None:
    message_id = message.get("id")
    thread_id = message.get("threadId")
    payload = message.get("payload")
    if not isinstance(message_id, str) or not isinstance(thread_id, str) or not isinstance(payload, dict):
        return None
    headers = _headers(payload)
    from_value = _decoded_header(headers.get("from"), 500)
    sender_name, sender_email = parseaddr(from_value or "")
    sender_email = sender_email.strip().lower()
    if not sender_email or "@" not in sender_email:
        return None
    recipients = {
        address.strip().lower()
        for _, address in getaddresses(
            [
                value
                for value in (
                    headers.get("to"),
                    headers.get("cc"),
                    headers.get("delivered-to"),
                )
                if value
            ]
        )
        if address and "@" in address
    }
    received_at = None
    internal_date = message.get("internalDate")
    if isinstance(internal_date, str):
        try:
            received_at = datetime.fromtimestamp(int(internal_date) / 1000, timezone.utc)
        except (ValueError, OverflowError, OSError):
            pass
    if received_at is None and headers.get("date"):
        try:
            received_at = parsedate_to_datetime(headers["date"])
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=timezone.utc)
            received_at = received_at.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            pass
    if received_at is None:
        return None
    plain, html = _body_candidates(payload)
    body = _normalize_plain_text("\n\n".join(plain)) if plain else _html_to_text("\n".join(html))
    return ParsedGmailMessage(
        gmail_message_id=message_id[:255],
        gmail_thread_id=thread_id[:255],
        sender_name=_decoded_header(sender_name, 160),
        sender_email=sender_email[:320],
        recipient_emails=frozenset(recipients),
        subject=_decoded_header(headers.get("subject"), 200),
        body=body,
        received_at=received_at,
        rfc_message_id=_decoded_header(headers.get("message-id"), 320),
        in_reply_to=_decoded_header(headers.get("in-reply-to"), 320),
    )
