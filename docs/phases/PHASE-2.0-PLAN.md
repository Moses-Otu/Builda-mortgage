# Phase 2.0 — First Evidence Slice: One Payslip, End to End

Status: **Approved (revision 2). Each step is planned and approved separately.** · Date: 2026-11-18 · Baseline: `80a82e7`

> Upload one payslip PDF, preserve the original, classify it, extract its fields, store the
> extracted values with page-level provenance, and allow a human to review the results.

This slice cuts vertically through roadmap Phases 2 (ingestion and storage) and 3
(classification and extraction), but for **one document type only**. It deliberately leaves
out bank statements, P60s, SA302s, credit reports, cross-document reconciliation,
affordability, borrower uploads and LLM use.

Tags: **[V]** verified in the code or docs, **[R]** recommendation, **[D]** needs your decision.

## Amendment (2026-11-19): minimum viable implementation

The MVP principle applies: if a simpler implementation is secure and correct, use it.
Where this amendment and the sections below differ, the amendment wins.

- **Step 2-C is merged into 2-B.** The worker checks the original and the PDF (page count,
  encryption, limits) in a child process with a timeout. See
  [PHASE-2B-PLAN.md](PHASE-2B-PLAN.md), revision 3.
- **No server-side page images and no `document_pages` table.** The review screen (2-F)
  shows the original PDF with pdf.js and draws the extracted regions over it. Anchors keep
  the page number and coordinates.
- **The queue is minimal:** one claim function, a 10-minute lock and an attempt-number
  fence; no extra roles, heartbeats or operator commands.
- Steps now, renumbered: 2-A (done), 2-B (done), **2-C** classification and extraction, **2-D** review and save to the case, **2-E** the review screen. Deferred hardening is listed under Stage 4 of
  [ROADMAP.md](../ROADMAP.md).

## 0. Revision 2: approved decisions and binding requirements

Where this section and the rest of the plan differ, this section wins.

**Decisions:**

| # | Decision |
|---|---|
| P2-1 | PostgreSQL-backed queue |
| P2-2 | Azure Document Intelligence **general layout** behind a provider abstraction. **No** US pay-stub model. |
| P2-3 | The minimal Next.js review screen is included, as step 2-F |
| P2-4 | Accepted fields become traceable document-derived observations. **A payslip never creates an income source automatically.** |
| P2-5 | Identical SHA-256 content in the same case gets 409, **enforced in the database, including concurrent uploads** |
| P2-6 | Documents get their own permissions, separate from case and financial permissions |
| P2-7 | National Insurance numbers are not extracted |
| P2-8 | Document processing stays disabled for real borrower data until D10 is approved, and production **fails closed** |

**Binding requirements:**

- **Provider honesty:**
  - If no Azure resource is provisioned, implementation continues against **clearly labelled
    synthetic fixtures**.
  - Provider output is never fabricated and presented as real.
  - The live Azure integration is reported as **unverified** until it has run against a real
    resource.
- **Reprocessing lifecycle:**
  - Runs are never deleted.
  - A failed run never retires or invalidates the last successful extraction or its
    observations.
  - A replacement run supersedes the previous active values only once it has **succeeded and
    been explicitly activated** by the broker, after review, in one transaction.
  - Details and tests are in step 2-E's plan. The lifecycle is:
    `queued → running → succeeded | failed`. Then, for succeeded runs only:
    `candidate → active → superseded`. Activation, superseding the previous run, retiring its
    observations (`reprocessed`) and promoting the new accepted fields all happen in one
    transaction.
- **Provenance per extracted field:**
  - Each field keeps its document version, processing run, page number, source coordinates
    (with the page's unit and size), original text, normalised value, schema version,
    provider and model version (through its run), confidence where the provider gives one,
    and an **append-only review history**.
  - The database enforces the **referential integrity** of these links. It cannot verify
    that a highlighted region semantically matches the value, and the plan doesn't claim it
    can.
- **Four value layers, kept distinct:**
  1. raw extracted text;
  2. the normalised value;
  3. the broker-corrected value, with a reason, beside the original rather than replacing it;
  4. the accepted observation in `financial_observations`, linked to the fields it came from.
- **Financial model:**
  - A payslip is evidence *of* an income source.
  - The broker associates it with an existing income source, or creates one through the
    existing endpoint, before promotion.
  - No current-value flag and no reconciliation.
- **Security:**
  - PDF validation, size and page limits.
  - Private storage under generated keys.
  - Idempotent jobs with retry limits.
  - Worker leases and protection against duplicate concurrent processing.
  - A separate worker identity.
  - Fail-closed production gating.
- **Execution:**
  - Six steps (2-A to 2-F), each with its own plan for approval first.
  - After each step, a report covering files, migrations, security, tests, limitations and
    the proposed commit.
  - No step starts without approval.
- **Scope:** synthetic payslips only. No bank statements, affordability, multi-document
  reconciliation, advice, broader document support, microservices, workflow engine or
  unnecessary infrastructure.

## 1. Starting point [V]

- Nothing for documents exists yet: no `documents` module, blob storage, queue, worker or
  frontend. `SourceKind` has `declared` and `broker_entry`; the code comments reserve
  `document` for Phase 2.
- **Commitments already recorded for Phase 2:**
  - Document-derived values follow **processing-run semantics**: each run owns its values,
    and reprocessing retires the previous run's values in the same transaction (PHASE-1.2-PLAN §5).
  - The normaliser must call `ensure_applicable`. The static guard test already requires
    this of anything that constructs an observation.
  - There is no `is_current` flag. Reconciliation (Phase 7) decides which figure counts,
    using explicit rules.
  - Workers act as a system actor with an explicit capability allowlist, and never
    impersonate a user (ARCHITECTURE §5).
- **D10 (retention and erasure) is still open.** The requirements brief says it blocks real
  borrower documents. This slice therefore works on **synthetic documents only** (§7).
- D3 (job queue) and D6 (synthetic corpus) are due now. D4 (borrower access) and D5 (LLM)
  aren't needed for this slice.

## 2. Scope

**In:**
1. A broker uploads a PDF to a case, optionally for a named applicant. The original is kept
   byte-identical, with SHA-256, size, media type and uploader.
2. A transactional job queue and a worker process. Jobs are idempotent and retried.
3. Ingest: the document is safely parsed for page count, encryption and structure, and each
   page is rendered to an image for review.
4. Classification: deterministic rules decide `payslip` versus `unrecognised`, under a
   versioned rule set.
5. Extraction: a `DocumentExtractor` interface returns layout (text, lines and key–value
   pairs with page and region). A **versioned UK payslip schema** (`payslip/1.0.0`) maps
   that layout to typed fields deterministically. Every field carries an anchor: page,
   region, raw text and confidence.
6. Raw provider output is stored, hashed and linked to its processing run.
7. Review: the broker sees each field beside its highlighted page region and accepts,
   corrects (with a reason) or rejects it. Everything is audited.
8. **Promotion [D: P2-4]:** accepted payslip fields become `source_kind = 'document'`
   observations on an income source the broker chooses, with mandatory lineage to the
   fields they came from, owned by the processing run.

**Out:** other document types, borrower uploads and magic links (D4), LLM classification or
extraction (D5), corroboration or verification beyond the broker's own action (Phase 7),
fraud and tamper detection, evidence packs, Azure deployment and IaC (Phase 12), and
**any real borrower document**.

## 3. Decisions needed [D]

| # | Decision | Recommendation |
|---|---|---|
| P2-1 (D3) | Job queue | A PostgreSQL table using `FOR UPDATE SKIP LOCKED`, behind a `JobQueue` interface. Enqueueing happens in the same transaction as the domain write. No new infrastructure. |
| P2-2 | Extraction provider | Azure AI Document Intelligence **`prebuilt-layout`**, which gives text, lines, key–value pairs and polygons per page, plus our own deterministic payslip mapper. Its prebuilt pay-stub model is US-only, so it doesn't fit UK payslips. No custom model training. **Do you have, or want, a UK South Document Intelligence resource?** It is needed once, to record real provider output for the synthetic corpus. Without it, the slice uses hand-built layout fixtures, and the live adapter stays untested until you do. |
| P2-3 | Review UI in this slice | **Yes, minimal:** one Next.js case page with a document list and a field-by-field review beside the page image. It is the last step, so it can be cut without affecting the rest. Next.js is already the chosen frontend (ARCHITECTURE §6). |
| P2-4 | Promote reviewed fields into financial observations | **Yes.** This is what proves lineage reaches the financial model and closes the processing-run commitment. Only broker-accepted or corrected fields are promoted. |
| P2-5 | The same file uploaded twice to a case | Return 409 with the existing document's ID (matched by SHA-256 within the case). A deliberate re-upload as a new version waits for a later phase. |
| P2-6 | Who can upload | Brokers only (`document:write`, a new permission given to adviser, case administrator and firm admin). Borrower upload waits for D4. |
| P2-7 | NI number and other identifiers | **Not extracted** in this slice (data minimisation). Employee name is extracted only to check it matches the applicant, and is stored as erasable data. |
| P2-8 | Real documents before D10 | Document routes are **disabled outside local, test and dev environments** by configuration that production can't override. Only synthetic documents are used in the repo, CI and dev. |

## 4. Data model (new `documents` and `extraction` modules)

Every table is tenant- and case-scoped, uses composite tenant/case foreign keys and RLS like
the existing ones, and is classified in `schema_policy.py`.

| Table | Purpose | Integrity rules (database) |
|---|---|---|
| `documents` | A broker's upload: case, optional applicant, declared type (`payslip`), status | Unique `(case_id, sha256)` for P2-5 |
| `document_versions` | The immutable original: `sha256`, `size_bytes`, `media_type`, `blob_key`, `page_count`, `uploaded_by`, `scan_status` | Append-only (no UPDATE except ingest results, set once; no DELETE for the runtime role) |
| `document_pages` | One row per page: dimensions and rendered-image blob key and hash | Append-only |
| `jobs` | Queue: `kind`, `payload` (IDs only), `status`, `attempts`, `run_after`, `idempotency_key`, `last_error_code` | Unique `idempotency_key`; error codes only, never messages |
| `processing_runs` | A pipeline run for a document version: classifier and schema versions, extractor/provider/model version, provider request ID and region (R9), raw-output blob key and hash, execution status, activation state | **At most one active run per document version** (partial unique index); only a succeeded run can be activated; runs are never deleted |
| `extracted_fields` | `field_code`, `raw_text`, normalised typed value (the same value columns as observations), `confidence`, **anchor** (page, polygon, page unit and size) | An anchor is required; composite foreign keys tie the field to its run, the run's document version and that version's page. The row is immutable. |
| `field_reviews` | Append-only review history: decision (`accepted` / `corrected` / `rejected`), corrected typed value, reason, reviewer, time | Insert-only for the runtime role; a field can't be reviewed once it has been promoted |
| `observation_sources` | Observation ↔ extracted field (many to many) | **A `document` observation must have at least one source** (deferred constraint trigger); the field must belong to the observation's run |

`financial_observations` changes:
- `source_kind` gains `document`, and a nullable `processing_run_id` that is required
  exactly when the source kind is `document`.
- `retired_reason` gains `reprocessed`.
- Document values sit outside the 0009 current-value index, as designed. Their own rule is
  **one active run per document version**, and reprocessing retires the old run's values in
  the same transaction, enforced by a deferred check like the withdrawal one.

Blob layout (private container, no public access, no SAS tokens given to browsers):
`{tenant_id}/{case_id}/{document_id}/{version_id}/original`, `…/pages/{n}.png` and
`…/runs/{run_id}/provider.json`. File names never appear in blob paths or logs. The API
always checks the database row (under RLS) before reading a blob; the path alone is never
trusted.

## 5. Flow

```
POST /cases/{id}/documents (multipart)
  └─ API: size cap, PDF magic bytes, SHA-256 while streaming → blob (original)
     → documents + document_versions + audit + enqueue(ingest), one transaction
worker: ingest      → parse in a separate process with limits; page count, encryption,
                      render pages → document_pages; enqueue(classify)
worker: classify    → rules v1 over extracted text → payslip | unrecognised
worker: extract     → DocumentExtractor (layout) → raw output → payslip schema 1.0.0
                      → processing_run + extracted_fields (anchors), atomically
broker: review      → GET fields + page images; POST accept / correct / reject per field
broker: promote     → choose income source → observations (source_kind=document)
                      + observation_sources, ensure_applicable, audit, one transaction
```

**Worker security:** the `jobs` table is tenant-scoped, so a worker can't claim across firms
under RLS. Claiming uses one narrow `SECURITY DEFINER` function, `app_claim_job()`. It
returns only the job ID, kind and tenant, and is covered by catalog tests. The worker then
opens that tenant's scope as a system actor whose capabilities are limited to
`document.ingest`, `document.classify` and `document.extract`. Workers can't review or
promote fields; those are broker actions.

## 6. Payslip schema `payslip/1.0.0` (deterministic)

| Field | Becomes measure | Notes |
|---|---|---|
| `employer_name` | — | Suggests the income source to link to; never auto-creates one |
| `employee_name` | — | Only checked against the applicant; erasable |
| `pay_date`, `period_start`, `period_end`, `pay_frequency`, `tax_period` | period key | Frequency is inferred deterministically from the dates and the tax period, or `unknown` |
| `tax_code` | — | Kept as an attribute, not a measure |
| `gross_pay` | `gross_pay` | Required for a run to count as `extracted` |
| `net_pay` | `net_pay` | Required |
| `basic_pay`, `overtime` | same | Optional line items |
| `bonus` | `bonus`, qualifier `unspecified` | The payslip doesn't say what kind of bonus it is |
| `income_tax`, `national_insurance`, `pension_contribution`, `student_loan_deduction` | same | Optional |
| `ytd_gross`, `ytd_tax` | — | Stored for Phase 7 continuity rules; not promoted in this slice |

Arithmetic checks, such as gross minus deductions equalling net, are recorded as **flags
only**: they inform the reviewer but never change a status (Phase 7 owns corroboration).
Confidence is shown, but it **never** moves a status.

## 7. Security, privacy and compliance

- **Untrusted files:** the API never parses PDFs. It checks the size cap (configurable,
  default 20 MB), the `%PDF-` magic bytes and the declared media type. Parsing and rendering
  run only in the worker, in a subprocess with a timeout and a memory limit.
  Encrypted PDFs, and PDFs over the page cap (default 10), are rejected with a reason code.
- **Malware scanning:** a `MalwareScanner` interface. In Azure, Defender for Storage malware
  scanning (Phase 12). Locally, `not_scanned`. Processing a `not_scanned` version is allowed
  only in local, test and dev (tied to P2-8).
- **Byte-identical originals:** the hash is checked again on every download. Downloads are
  served as `attachment` with `nosniff` and `no-store`. The runtime identity has no blob
  delete permission in Azure (Phase 12); locally, tests assert that the application code
  never deletes.
- **Providers:** an allow-list in configuration. Each run records the provider, region,
  model version and request ID. **Customer documents are never used for training**, and
  Microsoft's Document Intelligence data-handling terms are to be confirmed in the contract,
  as already listed in the retention brief (R9, question 7).
- **Logs and errors:** IDs and reason codes only, never file names, extracted text or
  values. Provider error bodies are not logged.
- **Audit:**
  - Events: `document.uploaded`, `document.processing_failed`, `extraction.completed`
    (system actor), `extracted_field.reviewed` and `observation.recorded` (promotion).
  - Field values and reasons go in erasable `audit_event_details`.
- **Retention:** this adds categories 1–3 and 11 of the brief (originals, provider output,
  extracted fields, page images), so the inventory gets updated. No retention behaviour is
  built before D10.

## 8. Delivery steps (each its own review and commit)

| Step | Delivers | New technology (and why) |
|---|---|---|
| **2-A Storage and upload** | `documents` module, `BlobStore` interface (filesystem implementation for unit tests, Azure implementation), upload, download and list APIs, `document:write`, Azurite in Docker Compose | `azure-storage-blob` (official SDK); Azurite (Microsoft's local Blob emulator); `python-multipart` (FastAPI's file uploads) |
| **2-B Queue and worker** | `jobs`, `JobQueue`, `app_claim_job()`, a `meip-worker` process, retries with backoff and a dead state, idempotency, worker system actor; **Docker stack verified end to end** (an outstanding item) | None (PostgreSQL) |
| **2-C Ingest and pages** | Sandboxed parsing, page images, `document_pages` | `pypdfium2` (PDFium bindings, permissive licence; page count and rendering without system dependencies) |
| **2-D Classify and extract** | Classifier v1, `DocumentExtractor` with a fixture (replay) implementation and an Azure Document Intelligence implementation, `payslip/1.0.0`, `processing_runs`, `extracted_fields`, raw output storage, a synthetic corpus generator | `azure-ai-documentintelligence` (official SDK); `reportlab` as a **dev-only** dependency to generate synthetic payslips |
| **2-E Review and promotion** | Review API, `observation_sources`, the `document` source kind, reprocessing semantics | None |
| **2-F Review screen** (P2-3) | A Next.js app (TypeScript, App Router), a case document list, and a field review beside the page with the anchor highlighted | Next.js (chosen in ARCHITECTURE §6) |

Each step: plan detail, then implementation, then the full gates (ruff, mypy strict, tests,
isolation suite, migration round trip and drift), then a step report, then a commit on your
approval.

## 9. Acceptance criteria

1. An uploaded PDF downloads byte-identical, with matching SHA-256. Non-PDF, oversize and
   encrypted files are rejected with 415, 413 and 422 respectively and clear reason codes.
   A duplicate in the same case gets 409 with the existing ID.
2. The upload, its audit event and its job commit together or not at all (tested by forced
   failure).
3. **Jobs are idempotent:** running the same job twice, or a worker crashing mid-job and the
   job being retried, produces one run and no duplicate fields or pages (tested with real
   concurrent workers, without relying on the service lock).
4. Classification is deterministic and versioned. Every synthetic payslip layout (at least
   five) is classified `payslip`, and the synthetic non-payslips are classified
   `unrecognised`, so they go to review instead of being guessed.
5. **Every extracted field has a page, region, raw text, confidence, schema version and
   run**, enforced by the database. Golden-file tests compare each synthetic layout's
   fields with expected values.
6. Raw provider output is stored, hashed and linked to its run, and the run records the
   provider, model version, region and request ID.
7. **Processing runs:** at most one active run per document version. Reprocessing retires
   the previous run's document values (`reprocessed`) in the same transaction, enforced by
   the database.
8. **Review:** accept, correct and reject happen once each, are audited with a reason, and
   a correction keeps the extracted value alongside it. Status changes only by broker
   action; confidence never changes it.
9. **Promotion:** only accepted or corrected fields are promoted. Every `document`
   observation has at least one `observation_sources` row (database-enforced). It goes
   through `ensure_applicable`. Withdrawn items and deprecated measures are refused as they
   are today.
10. **Isolation:** documents, versions, pages, jobs, runs, fields and blobs can't be seen
    or reached across tenants, through the API, the database or blob paths. The isolation
    suite and the authorization matrix cover every new route. A worker can't act without a
    tenant scope.
11. With P2-8, document routes return 404 or are disabled in a production configuration
    (tested). The repository contains only synthetic documents.
12. The Docker Compose stack (Postgres, Azurite, migrate, API, worker) processes a synthetic
    payslip from upload through extraction (documented and run once). If P2-3 is accepted,
    the same payslip is also reviewed in the browser.

## 10. Risks

| Risk | Mitigation |
|---|---|
| UK payslip layouts vary widely | Deterministic mapping with flags; anything unrecognised goes to review, never a guess. Expand the corpus before adding an LLM (D5). |
| Synthetic data doesn't look like real payslips | Model the corpus on public payroll-software layouts; measure accuracy on real documents only after D10 and a DPA, behind the same review step |
| Provider output changes between model versions | Model version recorded per run; replay fixtures pinned; the live adapter is checked by a separate, opt-in test |
| PDF parser vulnerabilities | Worker-only parsing in a limited subprocess; the API never parses; pypdfium2 version pinned and updated |
| Scope creep into reconciliation | YTD fields are stored but not compared; arithmetic flags are informational only |
| Frontend adds a second toolchain | Isolated in `frontend/`, last step, can be cut (P2-3) |

## 11. What I need from you

1. Decisions P2-1 to P2-8, especially **P2-2** (whether there is an Azure Document
   Intelligence resource) and **P2-3** (UI in this slice or not).
2. Approval to start **step 2-A only**. Each later step comes back to you before I build it.
