# RecruitFlow AI

RecruitFlow AI is a local-first, AI-assisted recruiter email application with private resumes, grounded drafting/refinement, verified Gmail OAuth, one-at-a-time human-approved sending, sent/uncertain history, and same-thread follow-ups. Product scope and decisions live in [`docs/PROJECT.md`](docs/PROJECT.md).

## Prerequisites

- Python 3.12
- Node.js 20.9 or newer
- Docker Desktop with Docker Compose
- Windows PowerShell

Run all commands from the repository root unless a command changes directory.

## First-time setup (Windows PowerShell)

```powershell
Copy-Item .env.example .env
Copy-Item backend/.env.example backend/.env
Copy-Item frontend/.env.example frontend/.env.local

# Reuse the existing environment; create it only when absent.
if (-not (Test-Path .venv)) { py -3.12 -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt

Set-Location frontend
npm.cmd install
Set-Location ..
```

The example credentials are local placeholders. Change them before exposing PostgreSQL beyond localhost. Uploaded resumes default to the Git-ignored `data/resumes/` directory.

## Configure AI

Edit the ignored `backend/.env` file and set:

```dotenv
AI_PROVIDER=gemini
GEMINI_API_KEY=your-secret-api-key
GEMINI_MODEL=gemini-3.5-flash-lite
```

To use OpenRouter instead, set `AI_PROVIDER=openrouter`, `OPENROUTER_API_KEY`, and an explicit free model ID. There is no automatic provider or paid fallback. Keys stay in the backend; automated tests use mocks.

## Configure Google OAuth

In Google Cloud, enable the Gmail API and create an OAuth 2.0 Web application client. Add this exact authorized redirect URI:

```text
http://localhost:8001/api/gmail/oauth/callback
```

If the consent screen is in Testing, add your Gmail account as a test user. Then set these values in the ignored `backend/.env`:

```dotenv
FRONTEND_ORIGIN=http://localhost:3000
GMAIL_OAUTH_CLIENT_ID=your-web-client-id
GMAIL_OAUTH_CLIENT_SECRET=your-client-secret
GMAIL_OAUTH_REDIRECT_URI=http://localhost:8001/api/gmail/oauth/callback
GMAIL_TOKEN_ENCRYPTION_KEY=your-generated-fernet-key
```

Generate the encryption key once and retain it while the connection exists:

```powershell
& .\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

RecruitFlow requests only `openid`, `userinfo.email`, and `gmail.send`. OAuth tokens are encrypted in PostgreSQL. Deleting or changing the encryption key makes stored credentials unusable. Google OAuth apps in Testing can have short-lived refresh grants and are limited to configured test users.

For a safe local OAuth test, keep the API bound to `127.0.0.1`, confirm the Google consent screen lists exactly those three scopes, and use a Google test-user account you control. Connect from Settings and verify the displayed address. For an end-to-end send test, create a synthetic draft addressed to another account you control, start with no attachment, prepare the final review, compare every displayed field, and approve only when ready. Connecting, generating, saving, reopening, and preparing review never send. After a confirmed test, verify Gmail Sent mail and disconnect in Settings to revoke and remove the stored credentials.

To verify follow-ups safely, send a new synthetic initial email after applying migration `20261118_0006`, open it in History, generate a follow-up with optional guidance, review/edit/refine it in Compose, save it, and prepare a new final review. Confirm the locked recipient, `Re:` subject, authenticated sender, attachment, and conversation notice before explicitly approving. Verify the message appears in the same Gmail thread. Older RecruitFlow sends without a stored RFC Message-ID remain readable but intentionally cannot guarantee follow-up threading.

## Apply migrations

Start PostgreSQL, then apply all migrations:

```powershell
docker compose up -d postgres
docker compose ps
Set-Location backend
& ..\.venv\Scripts\python.exe -m alembic upgrade head
Set-Location ..
```

## Start locally

Use three PowerShell terminals from the repository root.

Terminal 1 — PostgreSQL:

```powershell
docker compose up -d postgres
docker compose ps
```

Terminal 2 — backend (`http://localhost:8001`, health at `http://localhost:8001/api/health`):

```powershell
Set-Location backend
& ..\.venv\Scripts\python.exe -m alembic upgrade head
& ..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

Terminal 3 — frontend (`http://localhost:3000`):

```powershell
Set-Location frontend
npm.cmd run dev
```

Stop PostgreSQL without deleting local data:

```powershell
docker compose down
```

## Test and build

Backend integration tests require PostgreSQL. By default, the fixture derives an isolated `_test` database from `DATABASE_URL`, refuses any database whose name does not end in `_test`, creates it when needed, and validates PostgreSQL-specific default uniqueness. Set `TEST_DATABASE_URL` only to override that derived test database. Docker PostgreSQL must be running.

```powershell
docker compose up -d postgres
Set-Location backend
& ..\.venv\Scripts\python.exe -m pytest

& ..\.venv\Scripts\python.exe -m alembic upgrade head --sql
& ..\.venv\Scripts\python.exe -m alembic check
Set-Location ..\frontend
npm.cmd run typecheck
npm.cmd run build
Set-Location ..
```

## Create future migrations

```powershell
Set-Location backend
& ..\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "describe change"
& ..\.venv\Scripts\python.exe -m alembic upgrade head
```

## Local-only identity boundary

The backend selects one fixed local owner from `LOCAL_OWNER_ID`; API clients cannot submit user IDs. This is not authentication. Before any production deployment, add authenticated server-side identity resolution, authorization for every resource, secure session handling, and a migration plan for existing local-owner records.

## Milestone 4 security and limitations

- Every email requires a saved draft, a final server-created review snapshot, and an explicit approval action. Generation, saving, review preparation, and page loads never send mail.
- A timeout after Gmail dispatch is recorded as `uncertain` and cannot be retried through the same approval; check Gmail Sent mail manually.
- Confirmed sent drafts are read-only. Read-only send metadata is retained for the next history/follow-up milestone.
- The app reads no inbox content and has no automatic, scheduled, bulk, follow-up, or background sending.
- Image-only resumes require OCR elsewhere or must be omitted from generation.
- The fixed `LOCAL_OWNER_ID` is not authentication. Keep both servers bound to localhost. Public deployment requires real authentication, session authorization, CSRF/cookie review, HTTPS, secure secret management, abuse controls, and a production security/privacy review.

