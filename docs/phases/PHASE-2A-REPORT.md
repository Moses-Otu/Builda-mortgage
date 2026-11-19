# Step 2-A — Document Storage and Upload: Report

Date: 2026-11-19 · Status: **Implemented; review items closed; awaiting commit approval** ·
Plan: [PHASE-2A-PLAN.md](PHASE-2A-PLAN.md) (approved) · Baseline: `80a82e7`

**Synthetic data only:** every test document is a tiny PDF labelled
`SYNTHETIC TEST DOCUMENT`. No real borrower data was used.

## 1. Verification results (final run, PostgreSQL 17.4, 2026-11-19)

| Gate | Result |
|---|---|
| Full test suite | **737 passed**, 1 deselected (the opt-in Azurite test), 0 failed, in 7 min 47 s |
| Coverage | **95.55 %** branch coverage (the gate requires 85 %) |
| Ruff | `ruff format --check`: 114 files already formatted. `ruff check`: all checks passed. |
| mypy | `--strict`: no issues in 100 source files |
| Isolation suite, run on its own | **115 passed** (105 before 2-A): RLS 38, catalog 31, cross-tenant 34, other 12 |
| Migrations (`test_schema.py`, 12 passed) | Upgrade to head, downgrade to base, upgrade again; ORM-to-migration drift; OpenAPI snapshot; every operation documented; the five data-protecting downgrade guards, including the new **0012 refusal, which leaves the revision and data unchanged** |
| Authorization matrix | 165 passed: 4 new document routes × 4 roles, plus the coverage check |
| Audit coverage | 37 passed: every mutating route writes exactly one event, including `document.uploaded` |
| Document tests | `test_documents.py` 34 passed; `test_documents_unit.py` 40 passed (plus the Azurite test below) |
| Azure adapter against Azurite (opt-in) | **1 passed** against Azurite 3.33.0, run locally with `npx`. The Azurite request log confirms the calls (for example 409 on the refused overwrite and 404 on the missing key). |

Note: an earlier run was interrupted when the machine slept, and two command-line tests timed
out. The results above are from an uninterrupted run.

## 2. Review items

### 2.1 Azure Blob configuration: verified versus unverified

| Property | What the code does | Verified against | Status in real Azure |
|---|---|---|---|
| No public (anonymous) access | `create_container(public_access=None)`; no SAS URLs issued; the API streams bytes after RLS and permission checks | **Azurite:** the container's `public_access` is `None` (asserted in the test) | **Unverified.** Account-level "allow blob anonymous access = disabled" must be set by IaC (retention brief R16) |
| HTTPS in transit | **New:** settings refuse to start in staging or production unless the connection string uses HTTPS on every endpoint | Unit tests (four refused strings, one accepted; the account key is never echoed in the error) | **Unverified.** "Secure transfer required" and minimum TLS 1.2 on the account (R16) |
| Not the emulator in production | **New:** staging and production refuse the Azurite development account and `UseDevelopmentStorage=true` | Unit tests | Not applicable |
| Encryption at rest | None in our code. Azure Storage encrypts all data at rest by default; Azurite doesn't model it. | **Not verifiable locally** | **Unverified.** Confirm, and choose Microsoft-managed or customer-managed keys, in Phase 12 (R16) |
| Create-only, no delete | The storage interface has no delete; writes use `overwrite=False`; a static test enforces both | **Azurite:** a second write to the same key is refused (409) and the first content is kept | **Not sufficient on its own** (next row) |
| Limited identity permissions | **Not yet.** A connection string with an account key carries **full account rights, including delete and overwrite**, although our code never uses them. | — | **Unverified and not met.** Production needs a managed identity with a role that can create and read blobs only, with deletion done by the retention process under another identity (R15) |

### 2.2 Versioning is deliberately limited to version 1

Each document has exactly one version in step 2-A:
- `document_versions.version_number` has `CHECK (version_number = 1)`.
- There is no endpoint, service function or CLI command that creates a second version or
  replaces an original. The API exposes one `original` per document.
- Re-uploading identical content returns 409. Different content becomes a **new, separate
  document**, never a new version.

Replacement or version creation is **not implemented**, and nothing in the code or the
documentation suggests it is.

### 2.3 Download auditing: a production-readiness requirement

Downloads of originals are **not logged** in 2-A. This is now recorded as requirement **R17**
in the retention brief, and in ARCHITECTURE.md, as a condition **before real borrower
documents are enabled**:
- Every download (and later every page-image view) records who, when, which document and
  which request.
- The log never contains file names, document contents or extracted values.

### 2.4 Orphan reporting stays read-only

- `meip-admin find-orphan-blobs` reads `document_versions.blob_key` as the owner role, lists
  storage keys, and prints the keys that have no row. It writes nothing anywhere.
- No automatic blob deletion exists:
  - Neither storage backend has a delete method, and a static test checks this, including
    that no `delete_blob`, `delete_container`, `rmtree` or `remove` call appears.
  - The only `unlink` in the storage layer removes the filesystem backend's own temporary
    file, never a stored key.
- Deleting orphans remains an operator decision until the retention process (D10) exists.

## 3. Full diff review

Every changed file was read.

**Defects found and fixed:**
1. **Duplicate detection was too broad.** `_is_duplicate_violation` matched any constraint
   whose name ends in `sha256`, which includes the format check `ck_document_versions_sha256`.
   It now matches only `uq_document_versions_tenant_id_case_id_sha256` with SQLSTATE 23505.
   A unit test covers the unique index, the format check and the blob-key index.
2. **Deployed storage could have been plain HTTP, or the emulator.** It now fails closed (see
   2.1).
3. **The Docker quick start didn't mention creating the container.** The README now gives
   the `meip-admin init-storage` step.

**Checked and found consistent:**
- **Plan versus implementation:** the order of checks before the body is read, the streaming
  cap, the response headers, the grants, triggers and downgrade guard, the audit
  classification and the permissions all match the approved plan.
- **No security regressions:**
  - Existing permissions and the existing routes' behaviour are unchanged.
  - The new routes are in the authorization matrix and the cross-tenant suite.
  - A foreign case returns 404 before the body is read.
  - Logging stays limited to IDs.
- **Migrations:** 0012 is self-contained and reversible only when empty. The downgrade
  refuses rather than orphaning originals. Its names match the models, and the drift check
  passes.
- **Two intentional test changes outside 2-A:**
  - Two Phase 1.2 tests had "0011" hard-coded as the head revision; they now derive it.
  - The module-boundary rules gained a `documents` entry, and `financial` still may not
    import it.
- **No stray files:**
  - Test blobs go to a temporary directory that is removed afterwards.
  - Nothing under `.dev/` is tracked.
  - The Azurite data directory is in the session scratchpad, outside the repository.
- The Azurite connection string in `docker-compose.yml`, `.env.example` and the README is
  Microsoft's published development key. It is not a secret and is refused in staging and
  production.

## 4. Files

| | Files |
|---|---|
| **New** | `src/meip/documents/` (`__init__`, `models`, `schemas`, `service`, `storage`); `src/meip/api/routers/documents.py`; `migrations/versions/0012_documents.py`; `tests/integration/test_documents.py`; `tests/unit/test_documents_unit.py`; the Phase 2.0 plan, the 2-A plan and this report |
| **Changed (code)** | `core/config.py` (document and blob settings; fail-closed validation including HTTPS and no emulator when deployed); `core/errors.py`; `tenancy/permissions.py`; `api/deps.py`; `app.py`; `audit/service.py`; `models.py`; `cli.py` |
| **Changed (tests)** | `conftest.py`, `schema_policy.py`, `test_rls.py`, the authorization-matrix, cross-tenant and audit suites, `test_cli.py`, `test_schema.py`, `test_module_boundaries.py` |
| **Changed (other)** | `pyproject.toml` and `uv.lock` (`azure-storage-blob`, the `azurite` marker); `docker-compose.yml`; `openapi.json`; `.env.example`; README; ARCHITECTURE; the retention brief (category 1 status; new requirements R15–R17) |

Migration `0012_documents`, security design and test inventory: unchanged from the previous
version of this report and from [the plan](PHASE-2A-PLAN.md) §3.6 and §6.

## 5. Remaining limitations

1. **Storage identity:** account-key access can delete. A managed identity with create and
   read only is a Phase 12 requirement (R15).
2. **Azure account settings** (public access off at account level, HTTPS-only, TLS 1.2,
   encryption at rest, soft delete and versioning) are **unverified** until a real account
   exists (R16). The adapter has been verified **only against Azurite**.
3. **No download access log** until R17 is built. This is a precondition for real documents.
4. **Malware scanning:** every version is `not_scanned` until Azure Defender for Storage
   (Phase 12). Step 2-C will process `not_scanned` versions in local and test only.
5. **Orphaned blobs** can follow a crash between the storage write and the commit. They are
   reported, never deleted automatically.
6. **One version per document;** no replacement (see 2.2).
7. **A database connection is held while an upload streams** (at most 20 MiB). Rate limits
   and proxy body limits are Phase 12.
8. **The Docker stack has not been run.** That is the planned check in step 2-B.
9. **Synthetic only:** the D10 gate is a deployment control; the platform cannot tell
   synthetic documents from real ones.

## 6. Proposed commit (not made: awaiting your approval)

One commit on `main` with everything in section 4:

```
Phase 2.0 step 2-A: document upload and private, immutable storage
```

Step 2-B has not been started.
