# Phase 4 Report: Controlled Broker Pilot

**Date:** 2026-11-20 · **Status:** built and tested locally; **not committed**, awaiting review
(together with 3-C) · **Operating guide:** [PILOT-RUNBOOK.md](../PILOT-RUNBOOK.md)

> **This is not a compliance claim.** The technical controls below are in place. The platform
> is **not** legally compliant or production-ready on that basis alone. **No real borrower
> document may be used** until the external prerequisites in section 8 are complete.

## 1. What was built
| Area | Delivered |
|---|---|
| A. Real extraction | An OpenAI provider behind the existing `LayoutProvider` interface (`MEIP_EXTRACTION_PROVIDER=openai`). Payslips and bank statements, including scanned PDFs. No new document types |
| B. Data protection | Audit records for each original download and pack download, and for administrator password resets. Operator case erasure and a retention sweep. Isolated PDF rendering with temporary files always removed. Secrets only on the server. Bank statements stay refused outside local and test unless explicitly enabled |
| C. Authentication | The platform's own email and password accounts (`MEIP_AUTH_MODE=password`) replace dev sign-in in the pilot. The existing firm, membership and role model is unchanged |
| D. Deployment | `deploy/pilot/`: Docker Compose with Caddy (HTTPS), frontend, API, worker, migrations and PostgreSQL 17. Private networks, health checks, rotated logs, backup and restore scripts |
| E. Pilot readiness | Firm onboarding from the CLI (the first administrator gets a temporary password). The **Team** page, where firm admins invite advisers, reset passwords and remove access. First sign-in forces a password change. The broker workflow (upload, review, profile, approval, evidence pack) is unchanged |

Phase 1–3 architecture is unchanged. One approved safeguard ("the storage layer cannot
delete") was briefly broken by this work and then restored. Operator deletion now lives in
`meip.retention.blobs`, and `documents/storage.py` is identical to its committed version.

## 2. The OpenAI integration
**How it works** (`documents/openai_provider.py`, `documents/pdfrender.py`):
- **Rendering:** each page is rendered to an image in a **separate, isolated process** with a
  timeout, together with any text-layer segments. The temporary directory is always deleted.
- **Transcription:** one Responses API call per page with a strict JSON schema
  (`transcribe-cells/1`) and `store: false`. The model **only transcribes text cells and
  positions; it does no arithmetic and makes no decisions.** Classification, field extraction,
  checks and all calculations stay in the existing deterministic Python.
- **Provenance:**
  - a cell matched to the PDF's own text layer keeps the exact text and box, with confidence
    1.0;
  - a cell on a scanned page has a box from the model and **no confidence** (none is
    invented);
  - page numbers, source text and the raw output (model, prompt version, per-cell source)
    are kept, as before.
- **Failures:**
  - 408, 409, 429, 5xx and timeouts are retried with backoff, then become a transient job
    retry;
  - other errors and malformed or schema-violating responses mark the document "extraction
    failed" with a code;
  - no document text is logged.
- **Configuration:**
  - the key, model (`gpt-4.1` by default), base URL, timeout and attempts come from the
    environment;
  - when deployed, the key is required and the base URL must use HTTPS;
  - the synthetic provider remains for tests and is refused outside local and test.

**Tests:** 17 unit tests and 2 pipeline tests (document upload → worker → review), all passing:
schema, text-layer matching, scanned pages, retries, timeouts, malformed and refused output,
and no logging of content. **They use a mocked OpenAI API. No live OpenAI call has been made**
(there's no key in this environment), so real-document accuracy is **unmeasured**. See
section 9.

## 3. Authentication and security changes
- **Passwords:**
  - stored as scrypt hashes;
  - at least 12 characters, and not containing the email name;
  - an account locks for 15 minutes after 5 failed attempts;
  - the same error is shown for unknown accounts and wrong passwords.
- **Tokens:**
  - RS256, signed with a private key held only by the API container (`generate-signing-key`);
  - expire after 8 hours by default;
  - kept by the frontend in an httpOnly cookie, never seen by browser JavaScript.
- **Temporary passwords** (onboarding and resets) give a restricted token. It works only for
  changing the password; every other endpoint returns 403 `password-change-required`.
- **The `local_accounts` table** isn't directly reachable by the runtime role. Access goes
  through two `SECURITY DEFINER` functions (migration 0020).
- **Firm isolation:**
  - row-level security and per-request authorisation are unchanged;
  - new routes were added to the authorisation-matrix and cross-firm tests;
  - an administrator can't reset the password of someone who also belongs to another firm
    (409).
- **Dev shortcuts:** `/dev-sign-in` and the dev session route return 404 in production. The
  API accepts only platform-signed tokens in password mode.

## 4. Retention and deletion
- `meip-admin erase-case --tenant --case --reason --confirm`:
  - deletes every row of the case across 19 tables in one transaction, then the stored
    originals;
  - removes the audit details but keeps the id-only audit skeleton;
  - records a `case.erased` event.
- `meip-admin retention-sweep --days-after-closure N [--apply]`: a dry run by default. It has
  **no default period**, because the period is the D10 decision, which is still open.
- Documents remain **off** in a deployment until `MEIP_DOCUMENT_RETENTION_APPROVAL` records the
  approved decision.
- Outside the platform's reach (documented): downloaded files, backups until they expire, and
  OpenAI's own retention under its terms.
- Tests (4):
  - every case table is covered by erasure;
  - erasing a case removes all of it, including its stored originals, and nothing of other
    cases or firms, while keeping the audit skeleton;
  - original downloads are audited;
  - the sweep lists only cases closed long enough ago.

## 5. Deployment and hosting assumptions
See the [runbook](../PILOT-RUNBOOK.md), sections 2–4.
- **The host:**
  - one UK-region Linux VM with encrypted disks;
  - only ports 80 and 443 open;
  - Caddy obtains certificates automatically and sets HSTS.
- **What isn't exposed:**
  - PostgreSQL, the API and the document volume are on internal networks;
  - only the API and worker have outbound internet access, for OpenAI.
- **Secrets:**
  - in `.env` (mode 600) and one key file;
  - nothing secret is baked into images.
- **The containers:**
  - the backend and frontend run as non-root users;
  - migrations run before the API and worker start.
- **Lockfile:** `backend/uv.lock` was regenerated on 2026-11-22. ReportLab, httpx and Pillow
  moved from dev to runtime dependencies at the versions the tests used. `uv lock --check`
  passes.
- **Not done:** the stack hasn't been brought up with `docker compose` here.

## 6. Backup and restore verification
- `backup.sh` makes a PostgreSQL custom-format dump and a tar of the documents, with SHA-256
  checksums and optional gpg AES-256 encryption.
- `restore.sh` verifies the checksums, recreates the database, restores it and replaces the
  documents.

**Verified** on local PostgreSQL 17 with the synthetic demo data:
- the restore into a fresh database gave identical row counts (4 cases, 6 applicants,
  26 documents, 91 observations, 2 approvals, 135 audit events);
- both approval snapshots still match their hashes;
- ownership, all 26 row-level security policies and the runtime role's grants were intact;
- **all 26 restored originals match their recorded SHA-256**.

**Not verified:** the two scripts end to end on a Compose host. A restore drill on a spare VM
is listed before real data.

## 7. Test results
| Suite | Result |
|---|---|
| Backend full suite (PostgreSQL 17, includes 3-C), re-run on 2026-11-22 after the fix | **1,218 passed, 0 failed**, coverage **93.15%** (85% required). An earlier run (1,217 passed, 1 failed) caught the storage-delete regression described in section 1 |
| ruff check · ruff format · mypy (95 files) | all clean |
| Frontend vitest | **184 passed** |
| Frontend tsc · eslint | clean |
| Frontend production build | succeeded (14 routes) |

## 8. External prerequisites (outside the code)
The runbook's section 1 has the full table with owners.
1. A DPIA covering the platform, OpenAI as a processor, hosting, retention and erasure (and
   bank statements, if used).
2. Controller and processor roles, DPAs and pilot agreements with each firm.
3. OpenAI terms:
   - a DPA;
   - no training on the data;
   - retention terms (zero data retention if available);
   - the processing region and an international transfer mechanism.
4. A hosting agreement with UK data location, including for backups.
5. **D10 retention period** decided and recorded.
6. An independent penetration test of the deployed pilot.
7. Firm privacy notices updated, and an incident response with ICO breach reporting.

## 9. What still prevents a real-data pilot
**Blockers:**
1. **Every external prerequisite in section 8.**
2. **OpenAI accuracy unmeasured:** measure it on authorised (synthetic or consented)
   documents before brokers rely on extracted figures. Broker review stays mandatory either
   way.
3. **Deployment never run:** bring the Compose stack up on a test VM, run the restore drill
   there, then the pen test.

**Known gaps (accepted for a small pilot, worth deciding on):**
- **No MFA.** Sign-in is a password only.
- **No email delivery.** Temporary passwords are handed over out of band.
- **No malware scanning,** beyond the existing safe-PDF checks.
- **The worker shares the API's database role.**
- **Nothing outside the stack is monitored.**
- **The local demo database is still at migration 0019.** It only needs `alembic upgrade
  head`.
