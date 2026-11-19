# Mortgage Evidence Intelligence Platform

B2B SaaS for UK mortgage broker firms that turns borrower financial documents into a
reconciled, source-linked, broker-approved financial profile. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/ROADMAP.md](docs/ROADMAP.md).

**Current state: Stage 2-A (payslip reconciliation).** On top of the foundation (firms,
members, cases, applicants, the typed financial model, the audit trail and database-enforced
tenant isolation): the payslip workflow end to end (upload, safe processing, extraction with
a synthetic test provider, broker review, saving figures to the case), a broker screen, and
six versioned checks across the reviewed payslips and the declared basic salary. **Synthetic
data only.** See [docs/ROADMAP.md](docs/ROADMAP.md) and the reports in
[docs/phases/](docs/phases/).

## Repository layout

```
backend/            FastAPI service (Python package `meip`)
  src/meip/         core/ auth/ tenancy/ firm/ cases/ applicants/ financial/ audit/
                    documents/ reconciliation/ api/
  migrations/       Alembic migrations (run as the owner DB role)
  scripts/db/       Role and database bootstrap SQL (local only)
  tests/            unit/ integration/ isolation/
  openapi.json      Committed API contract snapshot
docs/               Architecture, roadmap, per-phase plans and reports
docker-compose.yml  Local stack: Postgres + migrations + API
```

## Quick start (Docker)

```bash
docker compose up --build
```

The API is at http://localhost:8000. Interactive docs are at http://localhost:8000/docs.

Then onboard a firm and get a token. Run these from `backend/` with a local Python environment
(see below):

```bash
MEIP_ENV=local MEIP_AUTH_MODE=dev MEIP_MIGRATIONS_DATABASE_URL=postgresql+asyncpg://meip_owner:meip_owner_local@localhost:5432/meip uv run meip-admin create-tenant --name "Example Brokers" --fca-frn 123456 --admin-subject alice --operator "your.name"
```

```bash
MEIP_ENV=local MEIP_AUTH_MODE=dev uv run meip-admin dev-token --subject alice
```

Call the API with `Authorization: Bearer <token>` and `X-Tenant-ID: <tenant_id>`.

Document uploads in Docker use the Azurite emulator. Create its private container once (from
`backend/`, with the Azurite connection string from `.env.example`):

```bash
MEIP_ENV=local MEIP_AUTH_MODE=dev MEIP_BLOB_BACKEND=azure MEIP_BLOB_CONNECTION_STRING="<Azurite connection string>" uv run meip-admin init-storage
```

`MEIP_ENV` is required, and there is deliberately no default. The development token issuer
(`MEIP_AUTH_MODE=dev`) is accepted only when `MEIP_ENV` is `local` or `test`.

Members added through `POST /api/v1/members` start as **invited**. They gain access only
after accepting with `POST /api/v1/me/invitations/{membership_id}/accept`, using their own
token. Invitations are listed by `GET /api/v1/me`.

## Local development

Requires Python 3.13, [uv](https://docs.astral.sh/uv/) and PostgreSQL 15+ (17 is what the project is tested on; Docker uses `postgres:17`).

```bash
cd backend
uv sync
cp .env.example .env
```

Bootstrap a native Postgres once, as a superuser. This creates the `meip_owner` and `meip_app`
roles and the `meip` database:

```bash
psql -U postgres -f scripts/db/roles.sql -f scripts/db/database.sql
```

```bash
uv run alembic upgrade head
```

```bash
uv run uvicorn meip.main:app --reload
```

## Review screen (frontend)

A Next.js broker screen for synthetic payslips (step 2-E, redesigned in Phase 2.1): create a
case and applicant, upload a payslip, review its figures beside the original (keyboard
shortcuts: press `?`), and save. It talks only to its own server, which forwards an
allowlist of API calls with the token from an httpOnly cookie. Run the API and the worker locally first (see above), then:

```bash
cd frontend
npm install
```

Create `frontend/.env.local` (git-ignored) containing:

```
MEIP_UI_DEV_SIGN_IN=true
MEIP_API_URL=http://localhost:8000
```

```bash
npm run dev
```

Open http://localhost:3000/dev-sign-in and paste a token from `meip-admin dev-token`. The
development sign-in exists only with `MEIP_UI_DEV_SIGN_IN=true` and never in a production
build. A synthetic payslip to upload: `uv run python scripts/make_synthetic_payslip.py`
(from `backend/`).

Frontend checks: `npm test` (vitest), `npm run typecheck`, `npm run lint`, `npm run build`.

## Quality checks

```bash
uv run ruff check src tests migrations
```

```bash
uv run ruff format --check src tests migrations
```

```bash
uv run mypy
```

Tests need a PostgreSQL superuser connection. The suite creates its own roles and the
disposable `meip_test` and `meip_test_migrations` databases:

```bash
MEIP_TEST_ADMIN_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres uv run pytest --cov
```

The Azure blob adapter has an opt-in test against the Azurite emulator. It is deselected by
default; run it with Azurite listening on port 10000:

```bash
MEIP_TEST_AZURITE_CONNECTION_STRING="DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;" uv run pytest -m azurite
```

After an intentional API change, regenerate the contract snapshot and review the diff:

```bash
UPDATE_OPENAPI_SNAPSHOT=1 uv run pytest tests/integration/test_schema.py
```

## Conventions

- **Tenancy:** every tenant-owned table has `tenant_id` and a row-level security policy. The
  runtime role `meip_app` owns nothing and cannot bypass RLS. Migrations and firm onboarding run
  as `meip_owner`. Every table must be classified in `backend/tests/isolation/schema_policy.py`.
  The catalog guard fails the build for an unclassified table, a changed policy, an extra grant,
  or an unsafe `SECURITY DEFINER` function.
- **Identity and privacy:** `users` holds identity only (issuer plus a stable object ID: Entra
  `oid`, not `sub`). Contact details live on each firm's membership and are never visible to
  another firm.
- **Authorization:** each tenant route declares its permission by taking a `*Ctx` dependency
  from `api/deps.py`. Services make only data-dependent checks, such as closing a case or
  assigning an adviser. `test_authorization_matrix.py` pins who may do what.
- **Who acts, where:** services receive an `OperationContext` = `TenantScope` (one
  tenant-bound transaction) + `Actor` (user, operator, system or integration). Background work
  uses `open_tenant_scope` with a system actor, recorded *on behalf of* the triggering user.
  It never impersonates them. Only `api/` may import FastAPI (`test_module_boundaries.py`).
- **Transactions:** mutating endpoints call `ctx.commit()` explicitly. Anything uncommitted is
  rolled back.
- **Concurrency:** single-resource responses carry `ETag: "<version>"`. Every change to an
  existing resource needs `If-Match`: a stale value gets 412, a missing one 428.
- **Case lifecycle:** `draft → collecting_evidence ⇄ in_review → (verified) → closed`.
  Closing needs a `closure_reason`, and reopening needs a note. `verified` cannot be stored
  until profile approvals exist (Phase 9).
- **Applicants:** never deleted. They are removed with a reason, and can be reinstated with
  the same ID. `party_role` (borrower or guarantor) is separate from `on_title` (ownership).
- **Financial values:** one current declared/broker value per item, measure, qualifier,
  source and period. Correct a value by superseding it (`supersedes_observation_id`), and
  withdraw items entered in error. Measure meanings never change. See
  `GET /api/v1/financial/measures` for value types, qualifiers and deprecations.
- **Financial data:** typed items (income sources, commitments with liable parties,
  expenditure) plus immutable `financial_observations`. Each observation's value type is
  fixed by `measure_definitions` and enforced by the database. Corrections supersede; they
  never edit.
- **Documents (Phase 2.0):** `POST /api/v1/cases/{case_id}/documents?document_type=payslip`
  with the raw PDF as the body (`Content-Type: application/pdf`) and an optional
  percent-encoded `Upload-Filename` header; personal data never goes in the URL. Identical
  content on the same case returns 409 `duplicate-document`. Originals are immutable, stored
  under server-generated keys, and checked against their SHA-256 on every download.
  **Synthetic documents only** until D10 is approved; document routes answer 404
  `feature-disabled` unless `MEIP_DOCUMENTS_ENABLED=true`. Stored originals with no database
  row (after a crash) are listed by `meip-admin find-orphan-blobs`; nothing deletes them.
- **Bank statements (Stage 2-B):** `document_type=bank_statement` (up to 20 pages), **local
  and test only**: refused with 404 `feature-disabled` anywhere else, whatever other settings
  say. Extraction keeps the summary (account last 4 digits, period, balances) and every table
  row (verbatim description, money in and out, running balance), each value with its page
  and box; it refuses statements with several accounts, a foreign currency or no table.
  The full account number and sort code are never stored in columns; a keyed fingerprint
  (`MEIP_ACCOUNT_FINGERPRINT_KEY`, fixed development key locally) tells accounts apart. They
  remain in the original and the raw provider output. **Confirmation (2B-2):**
  `POST …/documents/{id}/review` with the summary decisions and either `bank_account_id` or
  `new_account: {holder_ids, label}`; accounts are listed by `GET …/bank-accounts` and gain
  holders through `POST …/bank-accounts/{id}/holders`. Confirming saves no figures and never
  clears a balance discrepancy. The bank checks arrive in 2B-3. Demo statements: `bank_statement_series()` in
  `tests/synthetic_docs.py`.
- **Tests and `.env`:** the suite sets `MEIP_ENV_FILE` to empty (in `tests/__init__.py`), so a
  local `backend/.env` never leaks into tests. Outside tests, `MEIP_ENV_FILE` chooses the
  dotenv file (default `.env`; empty for none).
- **Processing (Phase 2.0):** each upload queues a job in the same transaction. The worker
  (`uv run meip-worker`, or the `worker` service in Docker) re-checks the original's hash
  and opens the PDF in a separate, time-limited process, then marks the document `ready`
  (with `page_count`) or `failed` (with `failure_code`). Transient failures are retried
  three times, one minute apart.
- **Extraction (Phase 2.0):** a `ready` document is classified with fixed payslip markers and
  extracted to schema `payslip/1.0.0`. The outcome is `extracted`, `unrecognised` or `failed`
  (`extraction_failed`). `GET …/documents/{id}/extraction` returns every value with its label,
  raw text, normalised value, page and boxes. The only provider is the **synthetic test
  provider** (`synthetic-fixture`), which staging and production refuse; Azure Document
  Intelligence can be connected later behind the same interface.
- **Review (Phase 2.0):** `POST …/documents/{id}/review` (`financial:write`, `If-Match`) takes
  one decision per extracted field (accept, correct with a value and reason, or reject with a
  reason) and an **existing** income source. Accepted and corrected financial fields become
  `source_kind = 'document'` figures linked to their decision; dates, frequency and tax code
  stay as reviewed evidence. All or nothing; a document is reviewed once (409 with the
  existing review afterwards). Later corrections use the financial endpoints.
- **Checks (Stage 2-A):** `GET /api/v1/cases/{id}/checks` runs rule set `payslip-checks/1`
  over the current reviewed payslips and declared basic salary, in the request; nothing is
  stored. Findings are `passed`, `discrepancy` (both sources cited) or `not_run` (with the
  reason), each with a key that changes whenever any cited input or the rule set changes.
  `POST …/checks/{key}/acknowledgement` (`financial:write`) records a note against a current
  discrepancy. **Acknowledging is not resolving or approving:** no figure changes and the
  finding is still reported as a discrepancy. Demo data: `scripts/make_synthetic_payslip.py
  series OUTDIR --faults ytd_break@1,missing_month@3`.
- **Audit:** every mutation writes exactly one `audit_events` row in the same transaction.
  The permanent row holds no direct identifiers and no financial values, though it is still
  pseudonymous personal data (status and role facts about an applicant ID). Amounts,
  names, reasons and notes go to the erasable
  `audit_event_details`. Direct identifiers (names, date of birth, email) are recorded only
  as "changed".
- **Money:** integer minor units (pence) with an explicit currency. Never floats.
- **Errors:** RFC 9457 `application/problem+json` with stable `urn:meip:problem:<slug>` types,
  always carrying `request_id`. Uniqueness races return 409 and other constraint violations
  return 422, never 500.
- **Logging:** structured JSON with IDs only. Requests are logged by route template, and
  exceptions by type and stack frames only, never by message, because driver errors embed row
  values. `test_log_safety.py` enforces this.
- **IDs:** UUIDv7.
