# RecruitFlow AI — Project Source of Truth

## Current product scope (V2)

RecruitFlow AI is a cost-conscious local web application for AI-assisted recruiter outreach. V1 established explicit per-message review and approval; V2 preserves that manual default while optionally allowing one explicit `Generate & Send Email` click to generate and immediately send initial outreach without preview. The app connects the user's Gmail account; maintains a structured Professional Profile, private attachment-only resumes, and email purposes; generates grounded drafts from Profile/job/purpose context; sends through Gmail; and tracks threaded history and replies.

## Architecture decisions

- Monorepo with `frontend/` (Next.js, TypeScript, Tailwind CSS) and `backend/` (Python 3.12, FastAPI).
- PostgreSQL is the system of record, accessed through SQLAlchemy 2.x and versioned with Alembic.
- Local PostgreSQL runs in Docker Compose; application processes run on the host for a fast development loop.
- Direct Google Gemini 3.5 Flash-Lite is the default local AI drafting provider through its HTTPS `generateContent` API. Optional fallback is disabled for fresh installs. Local development may explicitly opt into one paid Claude Haiku 5.5 fallback attempt after an eligible transient Gemini failure, subject to the Anthropic model allowlist and preflight cost guard. OpenRouter remains an explicitly selectable free-only alternative but is not part of the Gemini-to-Haiku route. API keys are never exposed to the frontend.
- Gmail uses Google's authorization-code flow and the exact configured callback. Only `openid`, `userinfo.email`, `gmail.send`, and `gmail.readonly` are requested. The verified Google identity is the sender; encrypted refresh/access tokens remain backend-only. Read access is used only for explicit synchronization of known RecruitFlow thread IDs.
- Manual sending is a two-step server-enforced workflow: create an immutable review snapshot, then explicitly approve that snapshot. The opt-in automatic initial-outreach path requires a separate user click, durable idempotency key, exact request/context binding, and the same Gmail send state machine; it is not scheduled or background work. PostgreSQL state transitions and uniqueness constraints prevent duplicate dispatch. Confirmed Gmail message/thread IDs are retained; ambiguous post-dispatch failures become non-retryable `uncertain` records.
- All configuration and credentials enter through environment variables. Secrets and uploaded resumes are never committed. Resume files use generated storage keys in an environment-configurable private directory outside public web roots.
- The frontend and backend run as separate local application processes. The current implementation has no queue, scheduler, or background worker.
- Milestone 2 uses one server-selected local development owner (`LOCAL_OWNER_ID`). Clients cannot supply or select an owner. This is strictly a local development boundary, not authentication; production deployment requires authenticated users, server-side identity resolution, authorization checks, and data migration away from the fixed local owner.

## Milestones

1. **Foundation (complete):** monorepo scaffolding, health endpoint, PostgreSQL/SQLAlchemy/Alembic setup, tests, build verification, and local documentation.
2. **Core data and settings (complete):** local sender profile, reusable purposes, secure multi-resume PDF storage, PostgreSQL migrations, owner isolation, default selection, CRUD APIs, and the responsive Settings dashboard.
3. **Compose and AI drafting (complete):** Professional Profile/job/purpose grounding, configurable Gemini/OpenRouter/Anthropic provider integration, strict structured generation, editable persistent drafts, regeneration, and responsive Compose UI. Resume PDFs are attachment-only and are not extracted for AI context.
4. **Gmail OAuth and human-approved sending (complete):** encrypted OAuth credentials, verified Gmail identity, disconnect/revoke, immutable final review, mandatory approval, RFC MIME sending with optional PDF, durable send states, duplicate protection, and confirmed message/thread metadata.
5. **History and follow-ups (complete):** owner-scoped sent/uncertain history, immutable conversation details, grounded follow-up drafting/refinement, explicit approval for every follow-up, and same-thread Gmail sending.
5.1. **Local deletion (complete):** owner-scoped draft removal and durable conversation tombstones that hide RecruitFlow records without changing Gmail messages or discarding send-safety metadata.
5.2. **Manual Gmail reply sync (complete):** read-scope-aware reconnection, bounded user-triggered reads of known non-deleted Gmail threads, safe incoming-message parsing and persistence, and chronological History display with local new-reply state.
5.3. **AI responses to received replies (complete):** grounded reply drafting from chronological sent/received context, optional user instructions, normal Compose editing/refinement, received-message-bound approval snapshots, and explicit same-thread Gmail sending.
5.4. **Controlled automatic sending, Stages A–B (complete):** persisted manual-by-default preferences, explicit opt-in metadata, exact draft/recipient/Gmail-identity/attachment-bound authorizations, transaction-safe UTC daily claims, and a separate user-triggered dispatch action. There is no scheduler or background dispatch.
6. **Production hardening:** real authentication/authorization, rate limits, observability, accessibility, privacy/retention controls, end-to-end tests, and deployment runbooks.

## Explicit non-goals for V1

- Automatic, scheduled, bulk, or background sending. Every outgoing message and follow-up requires an explicit user action and approval.
- Autonomous outreach agents, queues, workflow engines, or extra services.
- Non-Gmail providers, CRM/ATS integrations, campaign analytics, contact scraping, or lead discovery.
- Paid AI or infrastructure dependencies as a requirement for local development.
- Global inbox scanning/search, background reply polling, automatic retries, multi-user authentication, public deployment, scheduling, autonomous sending, and bulk/campaign sending.

## Implemented Milestone 2 contract

Milestone 2 includes a server-managed local profile, sender name, private PDF resumes up to 5 MB, reusable email purposes, one default resume and purpose per owner, secure resume download/deletion, PostgreSQL-enforced default uniqueness, and Settings UI workflows with loading, error, empty, confirmation, and success states.

Resume validation checks the upload media type plus PDF header and end marker. Storage keys are random and never exposed by the API; original filenames are reduced to safe basenames. Uploaded files live under `RESUME_STORAGE_PATH`, which is Git-ignored.

## Current API surface

- `GET /api/health`
- `GET|PUT /api/profile`
- `GET|PUT /api/settings/profile`
- `GET|POST /api/settings/resumes`
- `GET /api/settings/resumes/{id}/download`
- `PUT /api/settings/resumes/{id}/default`
- `DELETE /api/settings/resumes/{id}`
- `GET|POST /api/settings/purposes`
- `PUT|DELETE /api/settings/purposes/{id}`
- `PUT /api/settings/purposes/{id}/default`
- `POST /api/drafts/generate`
- `POST /api/drafts/generate-and-send`
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
- `POST /api/history/{conversation_id}/received/read`
- `POST /api/gmail/replies/sync`
- `POST /api/replies/generate`
- `POST /api/followups/generate`
- `GET|PUT /api/settings/sending-preferences`
- `POST /api/settings/sending-preferences/disable`
- `GET|POST /api/settings/automatic-authorizations`
- `GET /api/settings/automatic-authorizations/preview/{draft_id}`
- `DELETE /api/settings/automatic-authorizations/{authorization_id}`
- `POST /api/gmail/automatic-sends/{authorization_id}`

## Implemented Milestone 3 contract

Compose uses the persistent professional Profile, selected purpose, and user-entered recipient/job context. The Profile is the authoritative career-data surface and stores explicit education status, user-entered experience duration, employment types, categorized skills, validated links, and up to five ordered projects. Each project exposes only title, technologies, and a bounded free-text description. Migration `0016` conservatively appends distinct legacy project details to the description and retains the legacy columns as a staged data-safety measure. Uploaded PDFs remain available only as optional private Gmail attachments; no AI generation, refinement, follow-up, or reply path imports or calls PDF extraction. Dates never imply degree completion, experience duration, or employment type. Job descriptions, generated subjects, and generated bodies remain bounded.

Set `AI_PROVIDER=gemini` (the local default) with `GEMINI_API_KEY` and `GEMINI_MODEL`, or set `AI_PROVIDER=openrouter` with `OPENROUTER_API_KEY` and an explicit `OPENROUTER_MODEL` ending in `:free` (or the free router identifier). For explicit paid local quality testing only, `AI_PROVIDER=anthropic` selects the allowlisted `claude-haiku-5-5` Messages API integration when `APP_ENV=development`; it uses the same shared prompt and strict subject/body validator, JSON-schema output, low effort with thinking disabled, a 45-second bounded timeout, 768 output tokens, and a conservative configurable pre-request cost guard. The guard is a local estimate rather than an Anthropic billing cap. The verified local Gemini model is `gemini-3.5-flash-lite`; its request uses structured JSON output and `thinkingLevel: MINIMAL`. Other Gemini models do not receive that model-specific thinking control automatically. OpenRouter paid models are rejected.

Optional provider fallback is disabled by default. Local development may explicitly configure `AI_PROVIDER=gemini`, `AI_FALLBACK_ENABLED=true`, and `AI_FALLBACK_PROVIDER=anthropic` to attempt the allowlisted paid Claude Haiku model exactly once after one Gemini rate limit, timeout/connectivity failure, or confirmed upstream 5xx response. Anthropic fallback requires `APP_ENV=development`, a configured key, `claude-haiku-5-5`, and a positive preflight per-request cost ceiling (default `$0.01`); that estimate is not an Anthropic account-level billing cap. Missing credentials, authentication/permission/model configuration errors, invalid input, and malformed or ungrounded output never trigger fallback. No request chains to OpenRouter or another model in this Gemini-to-Haiku configuration.

Google AI Studio projects may be Free or Paid tier. Developers must confirm the selected project's tier and model pricing; application configuration does not prove billing status. Free-tier Gemini content may be used by Google to improve its products under Google's current terms. Bounded Profile, job, purpose, and applicable conversation context are sent to the explicitly selected AI provider only for synchronous generation or refinement. PDF resume text is not extracted or sent to AI providers. Prompts prohibit invented experience, employment, referrals, application status, connections, achievements, and company facts. Model output must be a JSON object containing bounded `subject` and `body` strings; recipient addresses, attachment choices, and sending behavior always come from validated application state.

Manual generation persists a local-owner `not_sent` draft immediately. Users may edit, save, regenerate, list, open, and delete unsent drafts. Sending remains a separate server-enforced action: either immutable review plus literal approval, or the explicitly enabled one-click initial-outreach endpoint described under V2.4. Profile contents, full prompts, API keys, and recruiter details are not logged by application code.

Initial outreach generation uses a grounded three-part first-contact structure when enough context exists: concise explicit professional background, a match-first comparison of actual job requirements to one or two relevant Profile experiences/projects, and one purpose-specific request. Every provider receives the same `CANDIDATE BACKGROUND`, `TARGET COMPANY/ROLE/JD`, `SELECTED PURPOSE`, and placeholder-only style sections. Completed education appears only for entries explicitly marked completed; in-progress and unspecified entries remain distinct, experience duration is user-entered, and internships remain typed as internships. Candidate evidence leads paragraph two before connecting to the target work. A deterministic validator rejects prohibited quantitative achievement metrics at provider and manual/refinement/automatic API boundaries. Manual generation and automatic initial sending share this exact one-call context path. New drafts store a stable Profile fingerprint, while their generated body and selected signature-link snapshot remain immutable through Gmail review; Profile edits cannot mutate approved content. GitHub, LinkedIn, and portfolio links default off in Compose and are appended deterministically only when selected. A PDF may be mentioned as attached only when server state confirms both resume selection and attachment preference.

## Implemented Milestone 4 contract

Google OAuth uses a cryptographically random, hashed, expiring, one-time state value, offline access, the configured exact redirect URI, and Google userinfo verification. The application never accepts a manually entered sender address as authenticated. Tokens are Fernet-encrypted using `GMAIL_TOKEN_ENCRYPTION_KEY`, refreshed server-side, never returned by APIs, and revoked on disconnect when Google is available. No application log includes credentials, prompts, resume content, recruiter details, or generated email content.

Compose prepares a server-side snapshot of the authenticated sender, validated recipient, subject, full plain-text body, and optional private PDF filename, byte size, resume ID, and SHA-256 digest. The digest and size are revalidated from private storage immediately before dispatch. Creating the snapshot does not send. A separate `{ "approved": true }` request is required, and any intervening draft edit marks pending reviews stale. Gmail delivery uses `users.messages.send` with an RFC-compliant UTF-8 MIME message. A database uniqueness guard plus `pending -> sending -> sent|failed|uncertain` transitions prevents repeat dispatch from concurrent clicks. A timeout or ambiguous read/write transport failure after dispatch is `uncertain` and is never automatically retried. Only a valid Gmail response produces `sent`; its message and thread IDs are persisted. Sent drafts are read-only.

Milestone 4 remains local-only. `LOCAL_OWNER_ID` is an isolation strategy, not authentication; CORS is restricted to `FRONTEND_ORIGIN`, and documented run commands bind the API to `127.0.0.1`. Public deployment requires authenticated identity/session resolution, per-request authorization, HTTPS, production secret/key management and rotation, OAuth consent/security review, CSRF/cookie review, abuse controls, audit/retention policy, and end-to-end security testing. OAuth consent screens in Google Testing mode are restricted to test users and Google may expire testing refresh grants.

## Implemented Milestone 4.1 contract

Compose can request one free-text AI refinement of the current editor subject/body, including unsaved manual edits, through the explicitly configured Gemini, OpenRouter, or local-only Anthropic Haiku provider. The backend rebuilds the same owner-validated Professional Profile, purpose, recipient/job, selected-link, and attachment-state context; treats the current email and refinement instruction as untrusted data; and applies the existing factual-grounding, plain-text formatting, structured-output, size, timeout, and provider safeguards. Resume PDFs remain attachment-only. Refinement returns a proposal only: it neither persists nor sends, approves, or changes Gmail state. Accepting a proposal creates unsaved editor changes, while Discard leaves the editor untouched. Saving accepted changes uses the existing draft update path and invalidates pending send reviews. Refinement is rejected for sent, sending, and uncertain drafts. No schema migration is required.

## Implemented Milestone 5 contract

History starts from immutable RecruitFlow send records and may include safely persisted incoming replies from an explicit manual sync. Conversation summaries and details are owner-scoped and distinguish Gmail-confirmed `sent` messages, ambiguous `uncertain` attempts, and received messages. Stored bodies and attachment filename/size metadata are displayed without private storage references. Search and pagination inputs are bounded.

Follow-up drafts are separate records linked to the original confirmed send. Generation uses the original message, prior confirmed RecruitFlow follow-ups, current explicit Profile context, purpose, job context, and optional guidance—never PDF content. The shared prompt explicitly states that no inbound replies were read and forbids invented recipient interactions. Follow-up recipient and normalized `Re:` subject are locked to the conversation; editing, refinement, attachment selection, save, immutable review, and explicit approval reuse the existing Compose workflow.

New outgoing messages receive an actual RFC Message-ID header that is stored with the send snapshot. A follow-up supplies the original Gmail `threadId`, stored RFC `In-Reply-To`, and accumulated `References`; Gmail resource IDs are never used as RFC Message-ID values. Only one pending/sending/uncertain follow-up may exist per conversation. Parent/thread/account changes make approval stale, and a returned Gmail thread mismatch becomes non-retryable `uncertain`. Messages sent before RFC Message-ID storage remain visible but cannot be followed up with guaranteed threading.

## Implemented V2.1 local deletion contract

Draft and History delete actions remove records from normal RecruitFlow lists and direct-access APIs only; they never call Gmail or delete Gmail messages. Drafts use a persistent `deleted_at` tombstone, and any pending review for a deleted draft becomes stale. Sent, uncertain, failed, and stale send records retain their immutable snapshots, Gmail identifiers, idempotency metadata, and audit state.

Deleting a History conversation transactionally tombstones its root send, RecruitFlow follow-ups, and all related drafts. Active `sending` conversations cannot be deleted. Deleted conversations cannot generate follow-ups or be approved for sending, and all normal draft/history endpoints exclude their tombstoned records. The root send tombstone is durable and must be honored by any future Gmail synchronization so a locally deleted conversation is not resurrected. Local deletion remains owner-scoped under the existing single local-owner boundary and has no effect on Gmail.

## Implemented V2.2 manual reply-sync contract

Gmail OAuth now also requests `gmail.readonly`; connections whose stored grant lacks that scope remain valid for sending but are explicitly reported as needing reauthorization before reply sync. Refresh-token rotation preserves the existing encrypted refresh token when Google omits a replacement, but a send-only token is never reused to claim a read-scope upgrade.

Reply sync occurs only when the user selects **Sync Replies**. One request reads at most 25 Gmail thread IDs already attached to confirmed, non-deleted RecruitFlow root sends by calling `threads.get(format=full)`. It performs no Gmail search, inbox listing, label change, deletion, attachment download, read-state mutation, polling, webhook, or background work. Per-thread failures are isolated and safely reported; repeated syncs are idempotent through a PostgreSQL owner/message uniqueness constraint.

Only messages in the expected thread, from the saved recruiter address solely to the verified Gmail identity, and not matching any stored RecruitFlow outgoing Gmail message ID are accepted. MIME text/plain is preferred; multipart/alternative and HTML-only content are converted to bounded plain text without rendering HTML, loading remote content, or downloading attachments. History merges received and sent records chronologically and tracks seen/new state only in RecruitFlow. Root conversation tombstones exclude deleted threads from selection and prevent locally deleted conversations from being resurrected. Synced content reaches AI only through the explicit V2.3 reply-generation or refinement actions described below.

## Implemented V2.3 AI reply contract

History exposes **Generate AI Reply** only when an active conversation has an eligible received message. The request accepts optional free-text instructions and sends the configured provider a bounded chronological view of RecruitFlow's outgoing messages, safely persisted incoming replies, and explicit Profile context. Incoming bodies, user instructions, Profile fields, purposes, and job descriptions remain untrusted model data; PDF content is never extracted. The shared prompt forbids fabricated availability, commitments, compensation expectations, interview outcomes, referrals, attachments, job facts, and recruiter statements; missing facts should produce a concise clarification instead. Structured subject/body validation and optional provider fallback remain unchanged.

Each reply draft stores the exact received-message ID it answers and opens in the existing Compose editor. The application, not AI output, fixes the recipient to the latest permitted inbound sender and normalizes the thread subject. Reply drafts support normal manual edits, persistence, proposal-only AI refinement, attachment selection, immutable review, and explicit approval. Generation, refinement, saving, and review preparation never send email.

The send review snapshots the received-message reference and a SHA-256 context fingerprint. Review and dispatch revalidate the live root tombstone, authenticated sender, Gmail thread, latest inbound ordering, participant identities, recipient, inbound content, latest outgoing parent, draft fingerprint, and attachment. A newer synced reply immediately makes a pending reply review stale. Confirmed replies use the original Gmail `threadId`, the inbound RFC Message-ID as `In-Reply-To`, accumulated stored RFC IDs in `References`, and the existing duplicate/concurrent-send and uncertain-delivery safeguards. One non-deleted draft or confirmed/uncertain send may respond to a given inbound message; subsequent replies require a newer synced message.

## Implemented V2.4 controlled automatic sending

All owners remain in persisted `manual` mode by default, including owners backfilled by migration `0011`. Switching to `automatic` requires an explicit confirmation stored with a consent timestamp, at least one independently enabled category (initial outreach or follow-ups), and a custom positive-integer UTC daily limit. New preferences default to 5, and both API and database validation enforce a maximum of 500. Migration `0012` changes only the constraint and default, preserving every existing owner's stored limit, permissions, authorizations, and claims. The limit is a safety maximum, not a guarantee of Gmail or AI-provider deliverability. Returning to manual mode or using the dedicated disable action revokes all active automatic-send authorizations.

An automatic-send authorization is a separate audit record bound to one owner, unchanged eligible draft, exact recipient, verified Gmail address and Google subject, exact optional attachment metadata/SHA-256, and either initial-outreach or follow-up semantics. Migration `0013` adds the attachment binding without changing prior authorizations or claims; older attached-draft authorizations must be recreated before dispatch. Received-message reply drafts are never eligible. Draft edits, draft/conversation tombstones, Gmail disconnect, category disablement, manual review creation, and explicit revocation invalidate or revoke authorization. Validation also rejects changed Gmail identity, recipient/content/attachment state, missing consent, deleted or sent drafts, invalid follow-up roots, and any pending, sending, sent, or uncertain attempt.

In manual mode, Compose retains `Generate Email -> edit/review -> approve -> send`. When automatic mode and automatic initial outreach are explicitly enabled, Compose instead shows an unmistakable warning and a `Generate & Send Email` action. That click is consent for one request: the backend binds a durable owner-scoped idempotency key to the exact submitted recipient, purpose, resume/attachment choice, job context, and verified Gmail identity. It generates and validates structured AI content, rechecks that purpose/profile/resume/Gmail context did not change, creates the exact draft authorization and immutable Gmail snapshot, claims UTC allowance, and dispatches through the shared MIME, row-lock, duplicate-prevention, and uncertain-outcome path. The generated text is not previewed before sending.

Migration `0014` adds durable one-click request records with `generating`, `dispatching`, `sent`, `uncertain`, and `failed` states. A repeated key with identical input returns the same confirmed or uncertain result without another AI or Gmail call; reuse with different input is rejected. AI or definitive pre-dispatch failures create no daily claim. Ambiguous Gmail outcomes remain `uncertain`, consume the claim, and are never retried automatically. Automatic sends do not populate the manual `approved_at` field.

One-click sending is limited to initial outreach. Follow-up and received-message reply generation, editing, review, and approval remain manual in the main UI. The previously implemented exact-draft follow-up dispatch safeguard remains fail-closed and performs one read-only `threads.get` for only its linked Gmail thread before claiming; missing read scope, read failure, mismatched participants, or a newer recipient response blocks it. Nothing scans the inbox.

A locked owner preference row serializes UTC limit checks, while database uniqueness prevents multiple claims for one authorization. `claimed`, confirmed `sent`, and ambiguous `uncertain` outcomes count against the limit; definitive failures are marked `released`. An uncertain outcome is never retried automatically. There is no worker, scheduler, polling dispatch loop, campaign engine, bulk import, or autonomous recipient selection.

