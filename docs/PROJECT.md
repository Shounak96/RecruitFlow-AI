# RecruitFlow AI — Project Source of Truth

## Product scope (V1)

RecruitFlow AI is a cost-conscious, production-quality web application for AI-assisted recruiter outreach. V1 will let a user connect their own Gmail account; maintain a sender profile, multiple resumes, and reusable email-purpose descriptions; provide recruiter and optional job context; select a purpose and resume; independently choose whether to attach that resume; generate and edit an email subject and body; explicitly approve every initial email and follow-up; send through Gmail; and view message/thread history with follow-ups kept in the original Gmail thread.

## Architecture decisions

- Monorepo with `frontend/` (Next.js, TypeScript, Tailwind CSS) and `backend/` (Python 3.12, FastAPI).
- PostgreSQL is the system of record, accessed through SQLAlchemy 2.x and versioned with Alembic.
- Local PostgreSQL runs in Docker Compose; application processes run on the host for a fast development loop.
- Direct Google Gemini is the default local AI drafting provider through its HTTPS `generateContent` API. OpenRouter remains an explicitly selectable alternative. `AI_PROVIDER` selects the primary provider; optional fallback is disabled by default and never selects a paid OpenRouter route. API keys are never exposed to the frontend.
- Gmail uses Google's authorization-code flow and the exact configured callback. Only `openid`, `userinfo.email`, and `gmail.send` are requested. The verified Google identity is the sender; encrypted refresh/access tokens remain backend-only.
- Sending is a two-step server-enforced workflow: create an immutable review snapshot, then explicitly approve that snapshot. PostgreSQL state transitions and a partial unique constraint prevent duplicate dispatch of one approval. Confirmed Gmail message/thread IDs are retained; ambiguous post-dispatch failures become non-retryable `uncertain` records.
- All configuration and credentials enter through environment variables. Secrets and uploaded resumes are never committed. Resume files use generated storage keys in an environment-configurable private directory outside public web roots.
- The frontend and backend remain separate deployable applications. No queue or worker is needed for V1.
- Milestone 2 uses one server-selected local development owner (`LOCAL_OWNER_ID`). Clients cannot supply or select an owner. This is strictly a local development boundary, not authentication; production deployment requires authenticated users, server-side identity resolution, authorization checks, and data migration away from the fixed local owner.

## Milestones

1. **Foundation (complete):** monorepo scaffolding, health endpoint, PostgreSQL/SQLAlchemy/Alembic setup, tests, build verification, and local documentation.
2. **Core data and settings (complete):** local sender profile, reusable purposes, secure multi-resume PDF storage, PostgreSQL migrations, owner isolation, default selection, CRUD APIs, and the responsive Settings dashboard.
3. **Compose and AI drafting (complete):** bounded PDF text extraction, configurable direct Gemini/OpenRouter integration, grounded structured generation, editable persistent drafts, regeneration, and responsive Compose UI.
4. **Gmail OAuth and human-approved sending (complete):** encrypted OAuth credentials, verified Gmail identity, disconnect/revoke, immutable final review, mandatory approval, RFC MIME sending with optional PDF, durable send states, duplicate protection, and confirmed message/thread metadata.
5. **History and follow-ups (complete):** owner-scoped sent/uncertain history, immutable conversation details, grounded follow-up drafting/refinement, explicit approval for every follow-up, and same-thread Gmail sending.
5.1. **Local deletion (complete):** owner-scoped draft removal and durable conversation tombstones that hide RecruitFlow records without changing Gmail messages or discarding send-safety metadata.
6. **Production hardening:** real authentication/authorization, rate limits, observability, accessibility, privacy/retention controls, end-to-end tests, and deployment runbooks.

## Explicit non-goals for V1

- Automatic, scheduled, bulk, or background sending. Every outgoing message and follow-up requires an explicit user action and approval.
- Autonomous outreach agents, queues, workflow engines, or extra services.
- Non-Gmail providers, CRM/ATS integrations, campaign analytics, contact scraping, or lead discovery.
- Paid AI or infrastructure dependencies as a requirement for local development.
- Inbox reading, automatic retries, multi-user authentication, public deployment, scheduling, automatic/bulk sending, and follow-ups are not part of Milestone 4.

## Implemented Milestone 2 contract

Milestone 2 includes a server-managed local profile, sender name, private PDF resumes up to 5 MB, reusable email purposes, one default resume and purpose per owner, secure resume download/deletion, PostgreSQL-enforced default uniqueness, and Settings UI workflows with loading, error, empty, confirmation, and success states.

Resume validation checks the upload media type plus PDF header and end marker. Storage keys are random and never exposed by the API; original filenames are reduced to safe basenames. Uploaded files live under `RESUME_STORAGE_PATH`, which is Git-ignored.

## Current API surface

- `GET /api/health`
- `GET|PUT /api/settings/profile`
- `GET|POST /api/settings/resumes`
- `GET /api/settings/resumes/{id}/download`
- `PUT /api/settings/resumes/{id}/default`
- `DELETE /api/settings/resumes/{id}`
- `GET|POST /api/settings/purposes`
- `PUT|DELETE /api/settings/purposes/{id}`
- `PUT /api/settings/purposes/{id}/default`
- `POST /api/drafts/generate`
- `POST /api/drafts/{id}/refine`
- `GET /api/drafts`
- `GET|PUT|DELETE /api/drafts/{id}`
- `GET /api/gmail/status`
- `GET /api/gmail/oauth/authorize`
- `GET /api/gmail/oauth/callback`
- `DELETE /api/gmail/connection`
- `POST /api/gmail/reviews/{draft_id}`
- `POST /api/gmail/sends/{send_id}`
- `GET /api/gmail/history`
- `GET /api/history`
- `GET /api/history/{conversation_id}`
- `DELETE /api/history/{conversation_id}`
- `POST /api/followups/generate`

## Implemented Milestone 3 contract

Compose uses the saved sender profile, selected purpose, optional selected resume text, and user-entered recipient/job context. PDF extraction is capped at 25 pages and 12,000 characters; empty, image-only, corrupt, and missing PDFs fail safely. Job descriptions, generated subjects, and generated bodies are also bounded.

Set `AI_PROVIDER=gemini` (the local default) with `GEMINI_API_KEY` and `GEMINI_MODEL`, or set `AI_PROVIDER=openrouter` with `OPENROUTER_API_KEY` and an explicit `OPENROUTER_MODEL` ending in `:free` (or the free router identifier). The verified local Gemini model is `gemini-3.5-flash-lite`; its request uses structured JSON output and `thinkingLevel: MINIMAL`. Other Gemini models do not receive that model-specific thinking control automatically. OpenRouter paid models are rejected.

Optional provider fallback is configured with `AI_FALLBACK_ENABLED=true` and `AI_FALLBACK_PROVIDER=openrouter` (or `gemini`). It attempts the distinct fallback provider exactly once only after the primary returns a rate limit, transport timeout, or confirmed upstream 5xx response. It does not retry or fall back for missing credentials, authentication/permission/model configuration errors, invalid input, or malformed/ungrounded structured output. Both providers must be configured independently, and enabling fallback does not establish or guarantee a free Gemini billing tier.

Google AI Studio projects may be Free or Paid tier. Developers must confirm the selected project's tier and model pricing; application configuration does not prove billing status. Free-tier Gemini content may be used by Google to improve its products under Google's current terms. Resume text and recruiter context are sent to the explicitly selected AI provider only for synchronous generation. Prompts prohibit invented experience, employment, referrals, application status, connections, achievements, and company facts. Model output must be a JSON object containing bounded `subject` and `body` strings; recipient addresses, attachment choices, and sending behavior always come from validated application state.

Generation persists a local-owner draft immediately. Users may edit, save, regenerate, list, open, and delete drafts. Every draft is `not_sent`; no backend endpoint or frontend action can send mail. Resume text, full prompts, API keys, and recruiter details are not logged by application code.

Initial outreach generation aims for roughly 90-140 words unless the selected purpose requires otherwise. The purpose drives one primary call to action; supported technical qualifications are limited to the most relevant 2-3. Resume, job-description, and custom-purpose text are treated as untrusted data. A draft may mention an attached resume only when server-validated state confirms both a selected resume and enabled attachment preference, and closings use only the saved sender name. Bodies use plain-text email layout with a standalone greeting, blank lines between concise paragraphs, a separated call to action where appropriate, and closing phrase and sender name on separate lines. Internal newlines are preserved through validation, PostgreSQL storage, API responses, and textarea editing.

## Implemented Milestone 4 contract

Google OAuth uses a cryptographically random, hashed, expiring, one-time state value, offline access, the configured exact redirect URI, and Google userinfo verification. The application never accepts a manually entered sender address as authenticated. Tokens are Fernet-encrypted using `GMAIL_TOKEN_ENCRYPTION_KEY`, refreshed server-side, never returned by APIs, and revoked on disconnect when Google is available. No application log includes credentials, prompts, resume content, recruiter details, or generated email content.

Compose prepares a server-side snapshot of the authenticated sender, validated recipient, subject, full plain-text body, and optional private PDF filename, byte size, resume ID, and SHA-256 digest. The digest and size are revalidated from private storage immediately before dispatch. Creating the snapshot does not send. A separate `{ "approved": true }` request is required, and any intervening draft edit marks pending reviews stale. Gmail delivery uses `users.messages.send` with an RFC-compliant UTF-8 MIME message. A database uniqueness guard plus `pending -> sending -> sent|failed|uncertain` transitions prevents repeat dispatch from concurrent clicks. A timeout or ambiguous read/write transport failure after dispatch is `uncertain` and is never automatically retried. Only a valid Gmail response produces `sent`; its message and thread IDs are persisted. Sent drafts are read-only.

Milestone 4 remains local-only. `LOCAL_OWNER_ID` is an isolation strategy, not authentication; CORS is restricted to `FRONTEND_ORIGIN`, and documented run commands bind the API to `127.0.0.1`. Public deployment requires authenticated identity/session resolution, per-request authorization, HTTPS, production secret/key management and rotation, OAuth consent/security review, CSRF/cookie review, abuse controls, audit/retention policy, and end-to-end security testing. OAuth consent screens in Google Testing mode are restricted to test users and Google may expire testing refresh grants.

## Implemented Milestone 4.1 contract

Compose can request one free-text AI refinement of the current editor subject/body, including unsaved manual edits, through the configured Gemini or OpenRouter provider. The backend rebuilds the same owner-validated sender, purpose, resume, recipient/job, and attachment context; treats the current email and refinement instruction as untrusted data; and applies the existing factual-grounding, plain-text formatting, structured-output, size, timeout, and free-provider safeguards. Refinement returns a proposal only: it neither persists nor sends, approves, or changes Gmail state. Accepting a proposal creates unsaved editor changes, while Discard leaves the editor untouched. Saving accepted changes uses the existing draft update path and invalidates pending send reviews. Refinement is rejected for sent, sending, and uncertain drafts. No schema migration is required.

## Implemented Milestone 5 contract

History is built only from immutable RecruitFlow send records; Gmail inbox/thread content is never read. Conversation summaries and details are owner-scoped and distinguish Gmail-confirmed `sent` messages from ambiguous `uncertain` attempts. Stored bodies and attachment filename/size metadata are displayed without private storage references. Search and pagination inputs are bounded.

Follow-up drafts are separate records linked to the original confirmed send. Generation uses the original message, prior confirmed RecruitFlow follow-ups, sender profile, purpose, relevant saved resume context, job context, and optional guidance. The shared prompt explicitly states that no inbound replies were read and forbids invented recipient interactions. Follow-up recipient and normalized `Re:` subject are locked to the conversation; editing, refinement, attachment selection, save, immutable review, and explicit approval reuse the existing Compose workflow.

New outgoing messages receive an actual RFC Message-ID header that is stored with the send snapshot. A follow-up supplies the original Gmail `threadId`, stored RFC `In-Reply-To`, and accumulated `References`; Gmail resource IDs are never used as RFC Message-ID values. Only one pending/sending/uncertain follow-up may exist per conversation. Parent/thread/account changes make approval stale, and a returned Gmail thread mismatch becomes non-retryable `uncertain`. Messages sent before RFC Message-ID storage remain visible but cannot be followed up with guaranteed threading.

## Implemented V2.1 local deletion contract

Draft and History delete actions remove records from normal RecruitFlow lists and direct-access APIs only; they never call Gmail or delete Gmail messages. Drafts use a persistent `deleted_at` tombstone, and any pending review for a deleted draft becomes stale. Sent, uncertain, failed, and stale send records retain their immutable snapshots, Gmail identifiers, idempotency metadata, and audit state.

Deleting a History conversation transactionally tombstones its root send, RecruitFlow follow-ups, and all related drafts. Active `sending` conversations cannot be deleted. Deleted conversations cannot generate follow-ups or be approved for sending, and all normal draft/history endpoints exclude their tombstoned records. The root send tombstone is durable and must be honored by any future Gmail synchronization so a locally deleted conversation is not resurrected. Local deletion remains owner-scoped under the existing single local-owner boundary and has no effect on Gmail.

