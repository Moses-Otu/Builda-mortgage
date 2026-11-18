# Mortgage Evidence Intelligence Platform

B2B SaaS for UK mortgage broker firms that turns borrower financial documents into a
reconciled, source-linked, broker-approved financial profile. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/ROADMAP.md](docs/ROADMAP.md).

**Current state: Phase 1 (foundation and core domain).** Firms, members, mortgage cases,
applicants, an append-only audit trail and database-enforced tenant isolation. No document
handling yet. See [docs/phases/PHASE-1-REPORT.md](docs/phases/PHASE-1-REPORT.md).

## Repository layout

```
backend/            FastAPI service (Python package `meip`)
  src/meip/         core/ auth/ tenancy/ cases/ applicants/ audit/ api/
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

`MEIP_ENV` is required, and there is deliberately no default. The development token issuer
(`MEIP_AUTH_MODE=dev`) is accepted only when `MEIP_ENV` is `local` or `test`.

Members added through `POST /api/v1/members` start as **invited**. They gain access only
after accepting with `POST /api/v1/me/invitations/{membership_id}/accept`, using their own
token. Invitations are listed by `GET /api/v1/me`.

## Local development

Requires Python 3.13, [uv](https://docs.astral.sh/uv/) and PostgreSQL 16+ (Docker or native).

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
- **Transactions:** mutating endpoints call `ctx.commit()` explicitly. Anything uncommitted is
  rolled back.
- **Audit:** every mutation writes exactly one `audit_events` row in the same transaction.
  Direct identifiers (names, date of birth, email) are recorded only as "changed".
- **Money:** integer minor units (pence) with an explicit currency. Never floats.
- **Errors:** RFC 9457 `application/problem+json` with stable `urn:meip:problem:<slug>` types,
  always carrying `request_id`. Uniqueness races return 409 and other constraint violations
  return 422, never 500.
- **Logging:** structured JSON with IDs only. Requests are logged by route template, and
  exceptions by type and stack frames only, never by message, because driver errors embed row
  values. `test_log_safety.py` enforces this.
- **IDs:** UUIDv7.
