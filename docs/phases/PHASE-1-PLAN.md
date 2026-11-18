# Phase 1 — Foundation & Core Domain: Implementation Plan

Status: **Agreed 2026-11-18 · Implemented, see PHASE-1-REPORT.md**

## 1. Repository inspection

- Working directory is empty; not a git repository. Greenfield build.
- Local tooling available: Python 3.13.1, Node 22.19, Docker 28.0, PostgreSQL 17.4 client, git 2.48. `uv` not installed.
- No existing conventions to preserve; conventions below become the baseline.

## 2. Scope

### In scope
1. **Repo layout** (monorepo):
   ```
   backend/            FastAPI service + workers (Python package `meip`)
     src/meip/
       core/           config, logging, db session, errors, ids, money types
       auth/           AuthProvider interface, OIDC JWT validator, dev issuer
       tenancy/        tenants, users, memberships, roles
       cases/          cases + lifecycle
       applicants/     applicants + case roles + declared data (minimal)
       audit/          append-only event log
       api/            routers, dependencies, error handlers
     migrations/       Alembic
     tests/            unit/ integration/ isolation/
   frontend/           (created Phase 2)
   docs/
   docker-compose.yml  postgres (+ azurite in Phase 2)
   ```
2. **Core infrastructure:** typed settings (pydantic-settings), structlog JSON logging with request ID / tenant ID / user ID context and PII-free policy, consistent error envelope (RFC 9457 problem+json), UUIDv7 IDs, health/readiness endpoints.
3. **Database:** SQLAlchemy 2 async models, Alembic migrations, two DB roles (`meip_owner` for migrations, `meip_app` for runtime, no RLS bypass), RLS policies on all tenant-owned tables.
4. **Tenancy & users:** `tenants` (firm: name, FCA FRN, status), `users` (external IdP subject, email, name), `memberships` (tenant, user, role: `firm_admin`, `adviser`, `case_administrator`, `read_only`). Role-based permission checks in a single policy module.
5. **Authentication:** `AuthProvider` protocol; production implementation validates OIDC JWTs (issuer, audience, signature via JWKS, expiry) — provider-agnostic, configured for Entra External ID. A **dev/test issuer** (local signing key) for local runs and tests; startup *fails* if it is enabled with `ENV=production`. Live Entra tenant wiring is deferred to Phase 2 (needs your tenant).
6. **Cases:** create, list (paginated, filtered by status/adviser), get, update metadata (purpose: purchase/remortgage/further advance/product transfer, property value estimate, loan amount sought, term), assign adviser, status transitions via explicit state machine (`draft → collecting_evidence → processing → in_review → approved → closed`; only transitions valid without later-phase features are exposed; others are defined but guarded).
7. **Applicants:** add/update/remove applicant on a case (name, DOB, employment status declared, declared gross income, declared monthly commitments), applicant role on case; max applicants configurable (default 4). Declared figures are stored as `declared` facts — the hook Phase 7 reconciles against.
8. **Audit:** every mutating request writes an audit event in the same transaction; DB trigger forbids UPDATE/DELETE on `audit_events`; `GET /cases/{id}/events`.
9. **API docs:** OpenAPI generated, tags per domain, examples on schemas.
10. **Quality gates:** ruff (lint + format), mypy `--strict`, pytest with coverage report; `make`-style task runner (`justfile` or plain scripts — no new tool required; I'll use a `scripts/` folder of PowerShell + sh scripts unless you prefer `make`).
11. **Containers:** backend `Dockerfile` (non-root, slim), `docker-compose.yml` for Postgres + API.
12. **Docs:** `README.md` (setup/run/test), `docs/phases/PHASE-1-REPORT.md` (delivered vs outstanding).

### Out of scope (explicitly)
Documents, extraction, any AI calls, frontend, Azure deployment, live Entra tenant, borrower access, email.

## 3. Dependencies & assumptions
- A1: Postgres runs locally via Docker Compose (also usable: the local Postgres 17 install).
- A2: One user may belong to multiple firms (memberships), but each request acts within exactly one tenant (selected via token claim or header validated against membership).
- A3: Firms are onboarded by a platform operator (seed/CLI command), not self-service sign-up, for the pilot.
- A4: `uv` will be installed as a dev tool (D2). If declined, I'll use `venv` + `pip-tools`.
- A5: No real personal data used anywhere in Phase 1; test data is synthetic.

## 4. Key design details

- **RLS mechanics:** each request opens a transaction and runs `SET LOCAL app.tenant_id = :tid` before any query; policies use `current_setting('app.tenant_id', true)::uuid`. Missing setting ⇒ zero rows (fail closed).
- **Not-found over forbidden:** cross-tenant resource access returns 404 to avoid existence leaks.
- **Optimistic concurrency:** `version` column on cases/applicants; `If-Match` / version in body; 409 on conflict.
- **Money type:** `Money(amount_minor: int, currency: Literal["GBP"])` from day one, so later phases inherit it.
- **Audit payload:** field-level diff of changed attributes with PII fields redacted to `"<changed>"`.

## 5. Risks & architectural implications

| Risk | Mitigation in Phase 1 |
|---|---|
| RLS misconfiguration silently allowing cross-tenant reads | Isolation test suite runs raw SQL as `meip_app` without tenant setting and with the wrong tenant, asserting zero rows |
| Async SQLAlchemy + `SET LOCAL` mismatch with pooling | Tenant setting scoped to transaction (not session); test with concurrent requests for different tenants |
| Dev auth issuer leaking into production | Hard startup failure + test |
| Over-modelling applicants before extraction exists | Keep declared data minimal; extend in Phase 4 via migration |
| Role model too coarse for larger firms later | Permissions centralised in one policy module so roles can become configurable |

## 6. Acceptance criteria

1. `docker compose up` starts Postgres + API; `/health` and `/ready` return 200.
2. `alembic upgrade head` from empty DB succeeds; `downgrade base` succeeds.
3. Authenticated user can create a case, add two applicants, update, transition `draft → collecting_evidence`, and list cases — covered by integration tests.
4. User from tenant B receives 404 for every tenant-A resource endpoint (parametrised test over all routes).
5. Raw SQL as `meip_app` returns zero tenant rows without/with wrong `app.tenant_id`.
6. Every mutating endpoint produces exactly one audit event; UPDATE/DELETE on `audit_events` raises.
7. Invalid/expired/wrong-audience tokens → 401; insufficient role → 403 (within own tenant).
8. Dev issuer + `ENV=production` → app refuses to start.
9. ruff, mypy `--strict`, and the full test suite pass; coverage ≥ 85% for `src/meip`.
10. OpenAPI document generated and committed snapshot test passes.
11. README lets a new developer run tests in < 10 minutes; Phase 1 report lists any outstanding issues.

## 7. Estimated size
~35–45 source files, ~25 test modules. Delivered as a single Phase 1 change set, plus `git init` with an initial commit if you want version control started.
