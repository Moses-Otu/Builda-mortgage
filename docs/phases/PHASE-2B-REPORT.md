# Step 2-B — Background Processing (minimum viable): Report

Date: 2026-11-19 · Status: **Implemented; review checks closed; committed** · Plan:
[PHASE-2B-PLAN.md](PHASE-2B-PLAN.md) (revision 3, approved) · Baseline: `d779f50`

**Synthetic data only.** Test PDFs are generated blank pages, or deliberately broken files,
each labelled `SYNTHETIC TEST DOCUMENT`.

## 1. Verification results

| Gate | Result |
|---|---|
| Full test suite (PostgreSQL 17.4) | **761 passed**, 1 deselected (the opt-in Azurite test), 0 failed, in 9 min 54 s (final run after the review checks) |
| Coverage | **94.59 %** (the gate requires 85 %) |
| Ruff | format: 118 files already formatted; lint: all checks passed |
| mypy | `--strict`: no issues in 103 source files |
| Isolation suite, run on its own | **118 passed** (115 after 2-A) |
| Migrations (`test_schema.py`) | 13 passed: round trip, drift, OpenAPI snapshot (regenerated), and the new 0013 downgrade refusal |
| Processing (`test_processing.py`) | 18 passed |
| Docker | **Outstanding verification, not a failure:** no container engine was available (section 5) |

One existing Phase 1 test, "firm admin has every permission", was replaced deliberately by
an exact-set test (section 6.1).

## 2. What was built

- **Enqueue with the upload:** the upload inserts a `document.ingest` job in its own
  transaction. If the upload fails, there is no job. `UNIQUE (kind, document_version_id)`
  makes enqueueing idempotent.
- **One claim function, `app_claim_job(lock_seconds)`** (`SECURITY DEFINER`, `search_path`
  pinned, EXECUTE revoked from PUBLIC):
  - It takes the oldest due job, or a `running` one whose lock has expired, with
    `FOR UPDATE SKIP LOCKED`, increments `attempts` and returns IDs only.
- **Fenced finish:** the worker writes its result and `UPDATE jobs … WHERE status =
  'running' AND attempts = <claimed attempt>` in one transaction. If the job has been
  claimed again since, nothing matches and nothing commits.
- **Retries:** storage errors and unexpected exceptions are retried after 1 minute, up to 3
  attempts. After that the document becomes `failed` with `retries_exhausted`.
  - A job whose worker died holding the lock three times is failed at the next claim.
  - Bad documents fail at once and are not retried.
- **The ingest job:**
  - It re-checks the SHA-256 (`hash_mismatch`, `missing_original`).
  - It opens the PDF with pypdfium2 **in a child process** with a 30-second timeout and
    nothing from `MEIP_*` in its environment (`unreadable_pdf`, `encrypted_pdf`,
    `too_many_pages` above 10, `timeout`).
  - It sets the document to `ready` (with `page_count`) or `failed` (with `failure_code`),
    writes one audit event (`document.processed` or `document.processing_failed`, system
    actor `worker`, `correlation_id` = job ID), and finishes the job.
  - Running it again on a processed document changes nothing.
- **The worker:**
  - `meip-worker` is a loop of claim, then the job's tenant scope (RLS on, as `meip_app`),
    then the handler, then the fenced finish and commit. It polls every 2 seconds when idle.
  - It refuses to start unless documents are enabled, under the same fail-closed settings
    as the API.
  - It finishes its current job before stopping.
- **Identity:** a new `document:process` capability is held **only** by the worker's system
  actor. Every role's permissions are now an explicit list (section 6.1).
- **API:** document responses gain `failure_code` and `page_count`. There are no new
  endpoints.
- **Docker Compose:** a `worker` service.

### What the concurrency guarantees are, and are not

- **Claiming:** a job is held by **one worker at a time**. `SKIP LOCKED` means two workers
  never claim the same job at the same moment.
- **Committing:** for each claim, **at most one worker's results are committed**. The
  attempt-number fence refuses a worker whose claim has expired.
- **Not exactly-once execution.** A job can *run* more than once: after a retry, or when a
  worker stalls past its lock and another takes over. The handler is therefore written to be
  safe to repeat. It does nothing if the document has already left `received`, and the
  stalled run's writes are rolled back. The outcome is applied once, even though the work may
  be attempted several times.

## 3. Migration `0013_document_jobs`

| | |
|---|---|
| `jobs` | Standard RLS, a composite foreign key `(tenant, case, version)` to `document_versions` (so a job can only point at a version in its own tenant and case), CHECKs on kind, status and attempts, a running job must hold a lock, a partial claim index. Runtime role: SELECT, INSERT, UPDATE; no DELETE. |
| `documents` | Statuses `ready` and `failed`; `failure_code` (closed list, required exactly when failed); `page_count > 0`; **column-level** UPDATE for the runtime role on `status`, `failure_code`, `page_count`, `updated_at` and `version` only |
| `document_versions` | Only `UNIQUE (tenant_id, case_id, id)` added, as the jobs' foreign-key target. Originals remain immutable. |
| Downgrade | Refuses while jobs exist or any document has left `received` (tested; revision and data unchanged) |

## 4. Tests

| Area | Tests (`test_processing.py`, 18) |
|---|---|
| Enqueue | One job per upload; enqueueing again is a no-op; a failed upload leaves no job and no document; **a forged job pointing at another firm's document is refused by the foreign key** |
| Ingest | A three-page PDF becomes `ready` with `page_count = 3`, one audit event (system actor, correlation ID) and the job `done`; running again gives `skipped`; encrypted, unreadable and 11 pages each fail with their code, **once**; tampered and missing originals; timeout |
| Concurrency | **8 concurrent claimers on 5 jobs: each job held by one worker at a time, 3 claimers get nothing.** A stale worker whose lock expired does all the work, but its fenced finish matches nothing and nothing is committed (no status change, no audit event); the current holder then completes normally. |
| Retries | Three transient failures (queued, `transient`, attempts 1 and 2), then `retries_exhausted` on the job and the document; a worker dying three times gives `retries_exhausted` at the next claim |
| Identity and fail-closed | Firm-admin permissions pinned exactly; every permission held by a role or system-only; the worker's imports limited to non-user-facing modules; no user role holds `document:process`; a user actor calling ingest is refused; the worker refuses to start with documents disabled (real subprocess); **the PDF child process receives no `MEIP_*` variables**; the worker stops when asked |
| Existing suites | Schema policy (`jobs`; column-level UPDATE on `documents`); the catalog's definer-function check now covers `app_claim_job` (pinned `search_path`, no PUBLIC EXECUTE); migrations (0013 downgrade guard); OpenAPI snapshot |

## 5. Outstanding verification (not test failures)

- **Docker verification is outstanding,** for deployment readiness. The Docker stack has not been run. No container engine is running on this machine:
  Docker Desktop's engine is unreachable and Podman has no machine set up. The `worker`
  service and image are configured but **unverified**. To run it: start Docker Desktop,
  then `docker compose up --build`, then `meip-admin init-storage` (see the README), then
  upload a synthetic PDF and expect `ready`.
- **Real Azure** remains unverified, as in 2-A.

## 6. Review checks

### 6.1 Firm administrator permissions

Firm admins were previously granted `frozenset(Permission)`, meaning **every permission,
including any added in future**. This was a wildcard since Phase 1, and the first 2-B draft
kept it (minus `document:process`). It is now an **explicit list**, so a new permission is
granted to no role until someone decides who holds it.

| Role | Exact permissions |
|---|---|
| Firm admin | `case:read`, `case:create`, `case:update`, `case:transition`, `case:close`, `case:assign_adviser`, `applicant:write`, `financial:write`, `member:read`, `member:manage`, `firm_settings:manage`, `audit:read`, `document:read`, `document:write` |
| Adviser | the same, except `member:manage` and `firm_settings:manage` |
| Case administrator | the adviser's, except `case:close` and `case:assign_adviser` |
| Read-only | `case:read`, `member:read`, `audit:read`, `document:read` |
| *(worker only)* | `document:process`: held by **no** role |

- **Effective permissions are unchanged** from step 2-A; the only new permission
  (`document:process`) is not granted to any user role.
- **Tests:** the firm-admin set is pinned as literal strings, and a test requires every
  permission to be held by some role or listed as system-only, never both. The 165-case
  authorization matrix passes unchanged.

### 6.2 Shared API database role: an intentional MVP compromise

The worker connects as `meip_app`, the API's role, rather than a dedicated worker role. This
is accepted for the MVP, under these conditions, each checked:

| Condition | How it holds | Evidence |
|---|---|---|
| The worker enforces its capability | `ingest` calls `ctx.require(document:process)` | A user actor calling ingest is refused (test) |
| It can't perform user-facing operations through the application | The worker has no HTTP routes and runs only the ingest job. Its module imports no case, applicant, financial, firm, member or upload service, and no HTTP framework. Its actor holds only `document:process`. | A new module-boundary test pins the worker's allowed imports |
| Tenant isolation remains enforced | All work runs in the job's tenant scope under RLS. A job can only reference a version in its own tenant and case (composite foreign key). | Forged cross-tenant job refused (test); isolation suite 118 passed |
| Recorded for production review | ROADMAP Stage 4 and ARCHITECTURE: introduce a dedicated worker database role if required before real data | — |

What it does **not** give: at the database level the worker can do anything `meip_app`
can, within a tenant. A defect in worker code could reach other tables. A dedicated role
would also limit that.

### 6.3 PDF child-process limits

| Limit | Enforced where | Tested |
|---|---|---|
| **Timeout** (30 s) | `subprocess.run(timeout=…)` kills the child on expiry; the document fails as `timeout` | Yes |
| **Page count** (at most 10) | In the child, after PDFium has loaded the document; more pages fail as `too_many_pages`. It bounds what follows, not the parse itself; the timeout bounds the parse. | Yes (11 pages) |
| **File size** (at most 20 MiB) | At upload, while streaming. The worker re-verifies the SHA-256 before the child sees the file, so the child can only ever open an original that passed the upload limit. It isn't re-checked in the child. | Yes (2-A streaming tests; hash re-check tests) |
| **Credentials** | The child's environment contains no `MEIP_*` variables | Yes |
| **Memory** | **None.** Recorded as a known limitation. | — |

## 7. Known limitations (deliberate, for the MVP)

1. **Shared database role** (section 6.2): a dedicated worker role is a production-readiness
   item, before real borrower data, if the review requires it.
2. **No memory cap on the PDF child process.** The timeout and the page and size limits
   bound the work but not peak memory. **Resource isolation must be reviewed again before
   real borrower documents are processed** (for example a container memory limit, or a
   separate sandbox).
3. **No operator job commands.** Failures show on the document the broker is looking at.
4. **Failed jobs are final.** There's no requeue; re-uploading the same content is refused
   as a duplicate. A requeue or retry action comes when the broker experience needs it.
5. **One job kind.** Extraction (step 2-D) adds the second.

## 8. Files

| | Files |
|---|---|
| New | `migrations/versions/0013_document_jobs.py`; `src/meip/documents/processing.py` (queue, ingest, worker); `src/meip/documents/pdfcheck.py` (child-process PDF check); `tests/integration/test_processing.py`; the 2-B plan and this report |
| Changed | `documents/models.py` (job model, statuses, failure codes), `documents/service.py` (enqueue with the upload, new response fields), `documents/schemas.py`, `core/config.py` (worker settings), `tenancy/permissions.py` (`document:process`, system-only), `audit/service.py`, `models.py`; tests: `conftest.py` (valid synthetic PDF helper), `schema_policy.py`, `test_schema.py`, `test_domain_rules.py`; `pyproject.toml` and `uv.lock` (`pypdfium2`, the `meip-worker` script); `docker-compose.yml` (worker); `openapi.json`; README; ARCHITECTURE; ROADMAP (the four stages); the Phase 2.0 plan amendment |

## 9. Proposed commit (not made: awaiting your approval)

```
Phase 2.0 step 2-B: background processing (queue, worker, safe PDF check)
```
