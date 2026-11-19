# Step 2-B — Background Processing (minimum viable): Plan (revision 3)

Status: **Approved and implemented (see PHASE-2B-REPORT.md)** · Date: 2026-11-19 · Part of [PHASE-2.0-PLAN.md](PHASE-2.0-PLAN.md)
· Baseline: `d779f50`

Principle: if a simpler implementation is secure and correct for the MVP, use it. This
revision replaces revisions 1 and 2, and absorbs the old step 2-C.

## What it does

After an upload, a background worker checks the stored PDF and marks the document `ready`
(with its page count) or `failed` (with a reason the broker can see). The next step adds
extraction as a second job on the same queue.

## Build

1. **A `jobs` table (migration `0013`):**
   - Columns: `id`, `tenant_id`, `case_id`, `kind` (`document.ingest`),
     `document_version_id`, `status` (`queued | running | done | failed`), `attempts`,
     `run_after`, `locked_until`, `error_code`, `created_at`.
   - Standard RLS. **`UNIQUE (kind, document_version_id)`** makes enqueueing idempotent.
   - Enqueued **in the same transaction as the upload**.
2. **One claim function, `app_claim_job()`:**
   - It is `SECURITY DEFINER`, because a worker has to find jobs across firms while RLS is on.
   - It takes the oldest due job, or a `running` one whose `locked_until` has passed, with
     `FOR UPDATE SKIP LOCKED`. It sets `running`, sets `locked_until = now() + 10 min` and
     increments `attempts`.
   - It returns `(job_id, tenant_id, document_version_id, attempts)`, which are IDs only.
3. **Fenced finish:**
   - The worker does its writes and
     `UPDATE jobs … WHERE id = :id AND status = 'running' AND attempts = :claimed_attempt`
     in one transaction. If no row is updated, a newer claim has taken over, and the
     transaction rolls back.
   - This stops two workers committing the same job, with no extra columns or functions.
4. **Retries:**
   - Storage errors and unexpected exceptions are retried after 1 minute, up to 3 attempts.
     After that the job fails, and so does the document, with `retries_exhausted`.
   - Permanent errors fail at once.
5. **The ingest job:**
   - It re-checks the original's SHA-256 (`hash_mismatch` or `missing_original` on failure).
   - It opens the PDF with **pypdfium2** in a **child process with a 30-second timeout**,
     and reads the page count and whether it's encrypted (`unreadable_pdf`, `encrypted_pdf`,
     `too_many_pages` if more than 10, or `timeout`).
   - **In one transaction** it sets `documents.status` to `ready` or `failed`, sets
     `failure_code` and `page_count`, writes one audit event, and finishes the job.
   - A document that is no longer `received` is skipped, so running the job again changes
     nothing.
6. **The worker:**
   - `meip-worker` is a loop: claim, act in the job's tenant scope as the system actor
     `worker`, with a new `document:process` capability that no user role holds. It polls
     every 2 seconds when idle.
   - It refuses to start unless documents are enabled, under the same fail-closed checks as
     the API.
   - It connects as `meip_app`, so RLS still applies.
7. **Document changes (migration `0013`):**
   - `documents` gains the statuses `ready` and `failed`, a `failure_code` column (with a
     CHECK) and a `page_count` column.
   - The runtime role gets column-level UPDATE on just those columns, plus `updated_at` and
     `version`.
   - Originals in `document_versions` stay untouched and immutable.
8. **The API:** the existing document responses gain `failure_code` and `page_count`. There
   are no new endpoints.
9. **Docker Compose:** a `worker` service is added. The stack is run once with a synthetic
   PDF, **if a container engine is running**; otherwise the report says it is unverified.

## Deliberately not built (with the reason)

| Item | Why it isn't needed now |
|---|---|
| Server-side page images and a page table | The review screen (2-F) shows the original PDF with **pdf.js** in the browser and draws the extracted regions over it. No image storage is needed. |
| A separate database role for the worker | The untrusted parsing runs in a child process that does no database work; RLS and the system actor's capability still apply |
| Lease heartbeats, lease tokens, extra definer functions | Jobs take seconds; a 10-minute lock plus the attempt-number fence is correct |
| Status-transition triggers | One service function is the only writer, and every transition is tested |
| Operator job commands, a dead-letter state | Failures show up on the document the broker is looking at |
| A memory cap on the parser | The timeout and the 10-page and 20 MB limits bound the work; a memory cap is a hardening item |
| Malware scanning | Production processing is blocked until D10 anyway; this is Stage 4 |

All of these are listed under Stage 4 in [ROADMAP.md](../ROADMAP.md), so none is lost.

## Tests

- **Enqueue:** a job is enqueued with the upload, and none if the upload fails; enqueueing
  twice gives one job.
- **Claiming:** several workers each claim a given job exactly once; a stale worker's commit
  is refused after another claim.
- **Retries:** retry, then `retries_exhausted`; permanent errors are not retried.
- **Ingest:**
  - Synthetic PDFs: one page and three pages become `ready` with the right count.
  - Failures: tampered, missing, encrypted, malformed, 11 pages and a timeout each give the
    right code.
  - Running it again is a no-op.
- **Security:**
  - `jobs` is in the RLS, catalog and schema-drift checks.
  - No user role has `document:process`.
  - The worker can't act outside the job's tenant.
- **Gates:** the full suite, ruff, mypy, migrations and drift.

**New dependency:** `pypdfium2` (the PDFium engine that Chrome uses; permissive licence; no
system dependencies), used only in the child process.
