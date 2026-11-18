# Phase 1 — Foundation & Core Domain: Completion Report

Date: 2026-11-18 · Status: **Ready for review after remediation.** All acceptance criteria
pass except #1 (Docker), which is written but could not be run here. A critical review found a
cross-tenant profile leak and other issues; see [Review remediation](#review-remediation).

## Delivered

| Area | What exists |
|---|---|
| Service | FastAPI app factory, ASGI entrypoint, typed settings (`MEIP_*`), JSON structured logging with request/tenant/user IDs, RFC 9457 problem responses, `/health` and `/ready` |
| Database | 6 tables, Alembic migration `0001`, two DB roles (`meip_owner` migrates, `meip_app` runs), RLS on every table, fail-closed `app_current_tenant()`, two narrow `SECURITY DEFINER` functions for the only cross-tenant lookups needed |
| Auth | `AuthProvider` interface, OIDC/JWKS JWT validation (RS256 only; checks iss/aud/exp/sub), local dev issuer refused in staging/production at settings *and* factory level |
| Tenancy | Firms, global users, per-firm memberships with 4 roles, central permission policy, last-admin protection, member add/role change/deactivate |
| Cases | Create/list (filter, paginate)/get/update, adviser assignment rules, full lifecycle state machine with later-phase transitions explicitly reported as unavailable, optimistic concurrency |
| Applicants | Add/list/get/update/remove, auto-positioned (1st, 2nd…), configurable maximum (default 4), declared income/commitments flagged as unverified |
| Audit | One event per mutation in the same transaction, append-only enforced by triggers for every role, identifier redaction, per-case history endpoint |
| Operations | `meip-admin create-tenant` and `meip-admin dev-token`, Dockerfile (non-root, multi-stage), `docker-compose.yml`, `.env.example`, README |
| API contract | OpenAPI generated with summaries/tags on every operation; snapshot committed at `backend/openapi.json` |

## Acceptance criteria

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | `docker compose up` starts Postgres + API | **Not verified** | Docker Desktop was not running in this environment. The API was verified as a real process instead: uvicorn + `meip-admin` against Postgres 17 (health, ready, onboarding, token, case creation, `/docs`) |
| 2 | Migrations upgrade from empty and downgrade to base | Pass | `test_schema.py::test_upgrade_downgrade_upgrade`; plus `test_models_match_migrations` (no drift between ORM models and migration) |
| 3 | Create case, add 2 applicants, update, transition, list | Pass | `test_case_flow.py` |
| 4 | Firm B gets 404 on every firm-A resource route | Pass | `test_cross_tenant_api.py`: 10 probes; a coverage test fails if a new parameterised route is not probed |
| 5 | Raw SQL as `meip_app` sees nothing without/with wrong tenant | Pass | `test_rls.py`: 6 tables × 2 cases, cross-tenant insert/update/move blocked, `row_security=off` refused, role cannot DDL, delete cases or create tenants |
| 6 | One audit event per mutation; audit UPDATE/DELETE fails | Pass | `test_audit.py`: all 8 mutating routes (enumerated from the OpenAPI schema), failed mutations write none, UPDATE/DELETE/TRUNCATE rejected for both roles |
| 7 | Bad tokens → 401; insufficient role → 403 | Pass | `test_auth.py` (expired, wrong aud/iss, foreign key, `alg=none`, missing `sub`, garbage); role tests in `test_cases.py` / `test_members.py` |
| 8 | Dev issuer + production → refuses to start | Pass | `test_core.py::TestSettings`, `test_auth.py::test_factory_refuses_dev_mode…` |
| 9 | ruff, mypy `--strict`, tests pass; coverage ≥ 85% | Pass | 161 tests pass; coverage 97.8% (branch); ruff + mypy strict clean on 67 files |
| 10 | OpenAPI snapshot test | Pass | `test_schema.py::test_openapi_matches_snapshot` |
| 11 | README lets a developer run tests quickly; report lists outstanding issues | Pass | `README.md`; this document |

## Deviations from the plan

| Plan said | Delivered | Why |
|---|---|---|
| `case_applicants` join table | Applicants owned by one case (`position` column) | Keeps borrower personal data scoped to the case it was collected for; cross-case borrower records can be added later if there is a real need |
| psycopg 3 driver | asyncpg | psycopg's async mode needs a selector event loop on Windows; asyncpg works with the default loop. Architecture doc updated |
| Task runner (`make`/scripts) | Plain `uv run …` commands in README | No extra tool needed; revisit when CI is added |
| "Declared figures stored as declared facts" | `declared_*` columns on applicants, documented as unverified | The fact model arrives in Phase 4; it will migrate these into facts |

## Bugs found and fixed during the phase

- **500 responses lost their request ID.** FastAPI's catch-all handler runs outside the request
  middleware. Unhandled errors are now handled inside the middleware, and a test asserts that the
  body's `request_id` matches the `X-Request-ID` header.
- **Migration downgrade failed:** the `users` RLS policy depends on `memberships`, so the drop
  order was fixed and covered by a test.
- **False coverage gaps:** SQLAlchemy's asyncio greenlets made coverage report executed lines as
  missed. Fixed with `concurrency = ["thread", "greenlet"]`.

## Outstanding issues and known limitations

1. **Docker stack not run.** Start Docker Desktop and run `docker compose up --build` to close
   criterion 1. The uv image tag in the Dockerfile (`0.13.9`) matches the local uv version but has
   not been pulled.
2. **No live Entra External ID integration.** OIDC validation is implemented and unit-tested with
   local keys, but has not been run against a real Entra tenant. Needs your tenant/app
   registration (planned for Phase 2). The identity claim is now `oid`, optionally pinned to your
   directory by `tid` (see remediation item 5).
3. **Invitations are addressed by IdP object ID.** Admins invite by `idp_subject` (the person's
   Entra `oid`), and the person accepts through the API. Emailing an invitation link, and
   declining, need the live IdP and a frontend, so they are deferred.
4. ~~Last-admin check has a race.~~ Fixed in remediation item 6.
5. **No CI pipeline.** The checks run locally. A GitHub Actions or Azure DevOps workflow is a small
   addition once a remote repository exists.
6. **No rate limiting or request size limits yet.** These belong at the Azure Front Door / API
   gateway layer (Phase 12) and on upload endpoints (Phase 2).
7. **Unhandled-exception logs include tracebacks.** Exception messages could in principle contain
   personal data. Acceptable for local development; production log handling (redaction,
   retention) belongs in Phase 12.
8. **All members see all firm cases.** This suits small firms. Per-adviser visibility rules can be
   added in the permission module if a pilot firm needs them.
9. **Windows test quirk.** pytest's `tmp_path` stalls about 30s per use on this machine (symlink
   creation), so the suite uses its own `tmp_dir` fixture.

## Test suite

- 161 tests: 46 unit, 115 integration/isolation, against a real PostgreSQL 17 instance.
- Runtime about 70s locally, mostly database creation and subprocess CLI tests.
- Isolation tests bypass the application and query as the runtime DB role, so tenant separation
  does not depend on application code being correct.

## Ready for Phase 2 (document ingestion & storage)

Decisions needed before planning: D1 Entra tenant details (for live auth), D3 job queue (recommend
a Postgres `SKIP LOCKED` queue), D4 borrower access model (recommend case-scoped magic links).


## Review remediation

A critical review before committing found one critical and several high-priority issues. These
nine agreed items were fixed. Migration `0001` was edited in place because Phase 1 had not been
committed or deployed anywhere.

| # | Finding | Change | Proven by |
|---|---|---|---|
| 1 | **C1: cross-tenant profile leak.** `users.email`/`display_name` were global; a second firm adding a known subject saw the first firm's data, and membership was granted without consent. | `users` is identity-only. Email and display name moved to `memberships`, per firm, under RLS. `app_register_user(id, issuer, subject)` returns only a UUID. New memberships are `invited` and grant no access. Only the invited identity can accept, via `POST /api/v1/me/invitations/{id}/accept` and the `app_accept_invitation` definer function, which is audited in the same transaction. A CHECK constraint `status <> 'active' OR accepted_at IS NOT NULL` stops admins force-activating. `/me` lists active memberships and invitations, plus the caller's own token claims. | `tests/isolation/test_profile_isolation.py` (7 adversarial tests), invitation tests in `test_members.py`, `test_catalog.py::test_users_table_holds_identity_only`. The previous test that accepted the leak was removed. |
| 2 | **H1: unsafe environment default.** | `MEIP_ENV` is required with no default. `auth_mode` defaults to `oidc`. Dev auth is allowed only for `env` local or test; the settings and the factory share one constant. The CLI reports configuration errors without a traceback or values. | `tests/unit/test_core.py::TestSettings` (17 cases, including missing env + dev, staging/production + dev, local without auth mode), CLI tests. |
| 3 | **H3: no catalog guard.** | `tests/isolation/schema_policy.py` classifies every table as tenant-scoped, special-policy or exempt (with reasons), with the exact runtime grants. `test_catalog.py` checks the live catalog: every table classified; RLS on; the exact policy text, command, roles and WITH CHECK; `tenant_id uuid NOT NULL`; the grant inventory; the runtime role has no superuser, BYPASSRLS, CREATEROLE or CREATEDB, no membership of a privileged role, owns nothing, and cannot CREATE or use TEMP; every `SECURITY DEFINER` function has a fixed `search_path` ending in `pg_temp`, is owned by `meip_owner`, and has no PUBLIC execute. `test_rls.py` now takes its tables from the classification. | 13 catalog tests. Verified by injecting an unclassified `documents` table and an unsafe definer function: both caught. |
| 4 | Applicant constraints | `UNIQUE (tenant_id, id)` on `applicants`. | `tests/isolation/test_constraints.py`: as the owner (no RLS), composite FKs reject an applicant pointing at another firm's case, an applicant moved to another firm, and a cross-firm adviser. |
| 5 | Identity claim | `MEIP_OIDC_SUBJECT_CLAIM` (default `oid`) and an optional `MEIP_OIDC_TENANT_ID` (`tid` must match). The subject must be a non-empty string. The dev issuer keeps `sub`. The key stays `(issuer, subject)`; Entra issuers embed the directory ID. | `test_auth.py`: same `oid` with different `sub` gives the same identity; missing, blank or non-string `oid` rejected; wrong or missing `tid` rejected. |
| 6 | Concurrency and errors | `update_applicant` locks the case row. The last-admin check locks the active admin rows (`FOR UPDATE`) before counting. `IntegrityError` is mapped: unique (23505) → 409, other constraints → 422. | `tests/integration/test_concurrency.py`: parallel admin demotions and deactivations keep at least one admin; parallel duplicate invitations give 201/409/409/409; same-version PATCHes give one 200; six parallel adds give positions 1–4 and 422s; an applicant edit blocks on a held case lock and then returns 409 once the case closed. A real asyncpg violation was confirmed to expose its SQLSTATE. |
| 7 | Authorization pattern | Each tenant route declares its permission with a `*Ctx` dependency (`api/deps.py::requires`). Services keep only the data-dependent checks (close case, assign adviser). No permission changed. | `tests/integration/test_authorization_matrix.py`: 17 probes × 4 roles against an explicit expected matrix, and a coverage test requiring every `/api` route. |
| 8 | Audit attribution | `audit_events.actor_ref` plus `actor_type 'operator'`, with a consistency CHECK. `meip-admin create-tenant --operator` (defaults to the OS user) is recorded as `operator:<name>`. | `test_audit.py` (operator attribution, invitee attribution, 5 invalid actor combinations rejected); `test_cli.py` checks the stored audit row. |
| 9 | API and log cleanup | Problem types are `urn:meip:problem:<slug>`. Exceptions are logged as type plus frames only, never messages, including chained causes. Unmatched paths are logged as `<unmatched>`, never raw. Integrity errors are logged by SQLSTATE and constraint name only. | `tests/integration/test_log_safety.py`: secrets in exception messages, causes, driver errors, URLs, invalid input and successful requests never appear in captured logs or responses. |

### Results

- Full suite: **298 passed**, with branch coverage of 96.4%. ruff and `mypy --strict` are clean
  on 74 files.
- Isolation suite (`tests/isolation`): 57 passed. Together with the authorization matrix and
  concurrency tests, 132 passed.

### API changes (pre-release, no clients yet)

- `MemberOut` gains `accepted_at`; `status` can be `invited`.
- `MeOut` gains `invitations`.
- `AuditEventOut` gains `actor_ref`; `actor_type` can be `operator`.
- New: `POST /api/v1/me/invitations/{membership_id}/accept`.
- Problem `type` values changed to URNs.
- Accepting an invitation increments the membership `version`.

### Remaining from the review (not in this remediation's scope)

- H5: a tenant scope and actor abstraction for background workers. Needed before Phase 2
  workers.
- H6: soft delete for applicants and stable positions. H7: the future of the declared figures.
- Business-rule decisions: case purposes and property use, applicant roles (JBSP, guarantor),
  closure reason codes and reopening, per-firm settings.
- `ETag`/`If-Match`, `Idempotency-Key`, production toggle for `/docs`.
- Deferred to hardening: `FORCE ROW LEVEL SECURITY` with a dedicated definer role,
  tamper-evident audit, column-level grants.
- Declining invitations and email delivery (needs the live IdP and a frontend).
