import hashlib
import json

from app.models import EmailDraft


def draft_fingerprint(draft: EmailDraft) -> str:
    canonical = json.dumps(
        {
            "draft_id": str(draft.id),
            "recipient_email": draft.recipient_email,
            "subject": draft.subject,
            "body": draft.body,
            "attach_resume": draft.attach_resume,
            "resume_id": str(draft.resume_id) if draft.resume_id else None,
            "conversation_id": str(draft.conversation_id) if draft.conversation_id else None,
            "reply_to_received_message_id": (
                str(draft.reply_to_received_message_id)
                if draft.reply_to_received_message_id
                else None
            ),
            "reply_context_fingerprint": draft.reply_context_fingerprint,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
