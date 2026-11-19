# Mortgage Evidence Intelligence Platform — Architecture

Status: **Draft for agreement** · Date: 2026-11-17 · Applies from Phase 1

## 1. What the system is (and is not)

A multi-tenant B2B SaaS that turns borrower financial documents into a **reconciled, source-linked, broker-approved borrower financial profile** for a mortgage case.

It is **not** an affordability/sourcing engine, a lending decision engine, or an advice tool. Any affordability output is an *indicative scenario* computed from transparent, versioned, broker-configurable assumptions.

The differentiator is **cross-document reconciliation with explainable provenance**, not OCR.

## 2. Architectural principles → concrete mechanisms

| Principle | Mechanism |
|---|---|
| Case is the primary entity | Every domain row (except tenant/user) carries `tenant_id` + `case_id`; APIs are case-scoped. |
| Multi-applicant cases | `case_applicants` join with role (`first_applicant`, `second_applicant`, …); facts attach to an applicant *and* the case. |
| Every figure traceable | `SourceAnchor` (document_id, document_version, page, bounding region, raw text) is mandatory on every extracted field; facts reference ≥1 anchor or an explicit broker-entry record. |
| Originals preserved | Blob storage is write-once (immutability policy + no delete permission for app identity); SHA-256 stored at ingest; derived artefacts stored separately. |
| AI separated from deterministic calc | Three layers that never mix: **Interpretation** (AI/provider output, stored raw + versioned) → **Facts** (normalised, typed, with verification status) → **Calculations** (pure functions over facts + a versioned rule set). Calculations never call an AI model. |
| Confidence ≠ proof | `confidence` is a separate field from `verification_status`; status changes only through deterministic corroboration rules or a broker action. |
| Verification status on every important fact | Enum: `extracted` → `single_source` / `corroborated` / `conflicting` → `broker_confirmed` / `broker_corrected` / `rejected`. |
| Broker responsibility | Approval is an explicit, attributable, audited act on an immutable snapshot of the profile. |
| No automated advice/decisions | No lender selection, no pass/fail outputs; affordability labelled "indicative" in API schema and UI. |
| Versioned, auditable rules | Rule sets and extraction schemas are code-defined, semver'd, and their version is recorded on every run. Runs are reproducible from (inputs snapshot, version). |
| No training on customer data | Only providers whose contractual terms exclude training/retention are allowed; enforced in a provider allow-list in config; documented in DPIA. |
| Future integrations | Export layer builds from a stable, versioned canonical JSON schema; transactional outbox for events/webhooks. |

## 3. Logical architecture

```
                ┌──────────────────────────────┐
  Broker  ───▶  │  Web app (Next.js)           │
  Borrower ──▶  │  broker console / upload UI  │
                └──────────────┬───────────────┘
                               │ HTTPS (OIDC bearer)
                ┌──────────────▼───────────────┐
                │  API (FastAPI)               │  auth, tenancy, case/applicant,
                │                              │  review, approval, export
                └───┬──────────────┬───────────┘
                    │              │ enqueue (transactional)
          ┌─────────▼───┐   ┌──────▼──────────────────────────────┐
          │ PostgreSQL  │   │ Workers (same codebase, Python)      │
          │ (RLS on     │◀──│  ingest → classify → extract →       │
          │  tenant_id) │   │  normalise → categorise → reconcile →│
          └─────────────┘   │  calculate → evidence pack           │
                            └──────┬──────────────┬───────────────┘
                     ┌─────────────▼──┐   ┌───────▼──────────────────┐
                     │ Private Blob   │   │ Provider adapters        │
                     │ (originals,    │   │  DocumentExtractor:      │
                     │  derived, packs│   │   Azure AI Doc Intel, …  │
                     └────────────────┘   │  LLMClient: Azure OpenAI │
                                          └──────────────────────────┘
```

Single deployable backend codebase (modular monolith) with API and worker processes. Modules map 1:1 to the 15 domains; module boundaries are enforced by package structure and import rules (no cross-module ORM access except via service interfaces). This avoids premature microservices while keeping later extraction possible.

## 4. Domain modules

| # | Module (`app/…`) | Owns | Phase |
|---|---|---|---|
| 1 | `tenancy` | tenants (firms), users, memberships, roles | 1 |
| 2 | `cases` | mortgage cases, status lifecycle | 1 |
| 3 | `applicants` | applicants, party roles, title flag, primary applicant, soft removal | 1, 1.1 |
| 6a | `financial` | income sources, commitments + liable parties, expenditure + parties, typed observations, measure definitions | 1.1 (declared/broker data); 2+ adds document lineage |
| 1a | `firm` | per-firm settings (applicant limit, four-eyes switch) | 1.1 |
| 14 | `audit` | append-only event log | 1 |
| 4 | `documents` | upload, blob storage, hashing, versions, pages | 2 (2-A: upload, storage, verified download) |
| 5 | `extraction` | classification, provider adapters, versioned schemas, raw outputs, source anchors | 3 |
| 6 | `facts` | normalised financial facts, provenance, verification status | 4 |
| 7 | `income` | income classification (employed basic/overtime/bonus/commission, self-employed, dividends, rental, benefits, other) | 5 |
| 8 | `transactions` | bank transaction parsing & categorisation | 5 |
| 9 | `liabilities` | commitments from credit reports + recurring bank outgoings | 6 |
| 10 | `reconciliation` | cross-document rules, discrepancies, tolerances | 7 |
| 11 | `rules` | versioned rule sets, indicative affordability calc | 8 |
| 12 | `review` | broker review, corrections, approval snapshots | 9 |
| 13 | `evidence` | evidence pack generation | 10 |
| 15 | `integrations` | canonical export schema, outbox, webhooks, CRM adapters | 11 |

## 5. Data & security foundations

- **Tenancy:** shared database, `tenant_id` on every tenant-owned table, PostgreSQL **Row-Level Security** enforced for the application role via `SET LOCAL app.tenant_id` per transaction. Application-layer scoping is the first line; RLS is defence in depth. The app DB role is *not* table owner and cannot bypass RLS.
- **Identity:** a user is keyed by `(issuer, subject)`. The subject claim is configurable and defaults to Entra's `oid`, which is immutable and the same across client applications. `sub` is not used because Entra issues a different value per client app. Entra issuers embed the directory ID, so the key is unique across directories, and `MEIP_OIDC_TENANT_ID` additionally pins tokens to one directory via `tid`. Only one issuer is supported; adding providers needs no schema change, because the issuer is already part of the key.
- **Personal data across firms:** `users` holds identity only. What a firm records about a person (email, display name) lives on that firm's `memberships` row, under RLS. Firms *invite* identities; access begins only when the person accepts with their own token. The two cross-firm lookups (`app_memberships_for_subject`, `app_accept_invitation`) and registration (`app_register_user`) are `SECURITY DEFINER` functions with a fixed `search_path`, no PUBLIC execute, and no profile data in their results.
- **Money:** integer minor units (pence) + ISO currency; `Decimal` in Python; never floats. Periods are explicit (`period_start`, `period_end`, `frequency`).
- **Identifiers:** UUIDv7 (time-ordered) primary keys; no sequential IDs exposed.
- **Audit:** append-only `audit_events` (DB trigger blocks UPDATE/DELETE/TRUNCATE) records actor (user, operator, system or integration, with `on_behalf_of_user_id` for work a user caused), tenant, case, action, entity, request or correlation ID, and a change set that holds **no direct identifiers and no financial values** (it is still pseudonymous personal data: status and role facts about applicant IDs). Values (amounts, reasons, notes) go in `audit_event_details`, which retention erasure can delete without touching the immutable log. Unknown fields default to the erasable class.
- **Actors and scopes:** domain services take an `OperationContext` (`TenantScope` + `Actor`). HTTP builds a user actor. Workers build a system actor with an explicit capability allowlist and never impersonate a user. Routes check their primary permission by dependency; workers check theirs per job kind.
- **Financial evidence model (1.1):** typed *items* (`income_sources` per applicant; `commitments` with `commitment_parties` for legal liability and an optional responsibility allocation; `expenditure_items`, household or individual) and immutable typed *observations*. `measure_definitions` fixes each measure's value type (money, signed money, rate, proportion, duration, count, date) and temporal kind (flow, stock, attribute). A composite FK and CHECKs enforce it. Liability, case association and inclusion in reconciliation are separate: removing an applicant never edits or reassigns their records. Phase 2 adds `source_kind = 'document'` with `observation_sources` lineage to extracted fields.
- **Financial value rules (1.2).** Each rule says where it is enforced. "Database" means it holds for any writer, including raw SQL as the runtime role and concurrent transactions; the service checks the same rules first to give clear errors.
  - **Immutable meaning (database).** Measure and qualifier definitions can only be added, or deprecated once. Deprecation stops new values for that measure or qualifier, never reinterprets old ones, and a new meaning needs a new code.
  - **Qualifiers (database).** `allowance` and `bonus` need a listed, non-deprecated qualifier. A qualifier is a single label, not an exhaustive or mutually exclusive classification; further dimensions will be separate attributes.
  - **Current values (database).** At most one active declared/broker value per (item, measure, qualifier, source, frequency, period start/end, tax year, as-of date), NULLs equal. Frequency and dates are part of the key, so a monthly and an annual figure, overlapping periods, or stock values at different as-of dates can each be current. This rule prevents duplicate entries; it does not define "the applicant's figure". That is decided in Phases 2–3 by reconciliation, using explicit rules over processing runs, evidence versions, broker-adopted values and assessment snapshots; there is deliberately no `is_current` flag. Document-derived values will follow processing-run semantics instead.
  - **Supersession (database).** Same key, predecessor retired as `superseded` in the same transaction, exactly one successor, no cycles; retirement is final.
  - **Withdrawal (database).** A withdrawn item, and its parties, are frozen; no value can be added to it, and a withdrawal cannot commit while the item still has active values. A value insert racing a withdrawal waits for it (row lock) and then fails.
  - **Withdrawal audit (application).** One `financial_item.withdrawn` event per withdrawal, in the same transaction (the service).
  - **Applicability (application only).** Whether a measure suits an item's kind is checked in the application through the single function `ensure_applicable`, not by the database. A static test requires every function constructing an observation to call it; it does not see Core `insert()` or raw SQL, so new writers must be reviewed.
- **Maintenance note.** The append-only and freeze triggers also apply to the owner role: a future migration that must UPDATE reference rows or withdrawn items has to say so explicitly and disable the specific trigger within that migration.
- **Audit-detail binding (1.2):** `audit_event_details` rows can only be written in the transaction that wrote their event (`audit_events.txid`, which the runtime role cannot set). COPY into row-level-secured tables is refused by PostgreSQL.
- **Retention and erasure:** requirements, the data inventory and the open decisions are in `docs/compliance/RETENTION-AND-ERASURE-REQUIREMENTS.md` (decision D10).
- **Documents (2.0, step 2-A):**
  - Originals live in private blob storage under keys generated from UUIDs only
    (`{tenant}/{case}/{document}/{version}/original`). The **application code** can only
    create and read: the storage interface has no delete and never overwrites. The **storage
    identity** is a separate matter: a connection string with an account key can delete
    anything, so production must use a managed identity whose role allows only create and
    read (Phase 12, see the retention brief R15–R16).
  - One version per document in Phase 2.0 (`version_number = 1`, enforced by a CHECK).
    There is no replacement or new-version operation.
  - `document_versions` rows are immutable for every role (trigger). `UNIQUE (tenant, case,
    sha256)` stops identical content twice in a case, including under concurrent uploads;
    the case lock means a duplicate is normally caught before anything is stored.
  - The API never parses a PDF: it checks the declared type, the `%PDF-` signature at byte 0
    and the size, while streaming. Parsing happens in the sandboxed worker (step 2-C).
  - Downloads re-hash the stored bytes before sending any of them.
  - **Fail-closed:** document routes return 404 unless enabled. Outside local and test,
    enabling requires a D10 retention-approval reference, the filesystem backend is
    refused, and the storage connection string must use HTTPS and must not be the Azurite
    development account; the service will not start otherwise.
  - **Processing (step 2-B, minimum viable):** a `jobs` table (RLS, `UNIQUE (kind,
    document_version_id)`), enqueued in the upload's transaction. Workers claim through one
    `SECURITY DEFINER` function, `app_claim_job()`, using `FOR UPDATE SKIP LOCKED`, with a
    10-minute lock. A worker finishes a job in the same transaction as its writes, fenced on
    the claimed attempt number, so a worker whose claim expired commits nothing. The PDF is
    opened only in a child process with a timeout and no credentials in its environment.
    The worker acts as the system actor `worker` with `document:process`, which no user role
    holds. **MVP compromise:** it connects with the API's database role (`meip_app`), so RLS
    still applies but the database doesn't narrow it further. A dedicated worker role is a
    production-readiness item (ROADMAP Stage 4). Role permissions are listed explicitly per
    role, never as "every permission". No server-side page images: the review screen renders the original with pdf.js.
  - **Extraction (step 2-C):** deterministic, no AI. Classification by fixed markers
    (`payslip-rules/1`: a net pay marker, at least two of six supporting groups, and no
    exclusion such as P60, P45, SA302 or bank statement). Extraction to `payslip/1.0.0`: one
    row per value with its own label, raw text, normalised value, value and label boxes, page
    size and unit, and confidence (an indicator only). Raw provider output is kept as JSONB.
    Runs and fields are insert-only, linked to their version within one tenant and case,
    with boxes CHECKed to lie inside their page. The database can't verify that a box shows
    its value; the broker does. Provider: `LayoutProvider` interface; only the test-only
    synthetic provider exists so far.
  - **Review (step 2-D):** one review per extraction run, with one decision per field.
    Corrections sit beside the original value, never overwriting it. Approved financial
    fields become `document` observations, and the database requires each to link to its
    decision (and only those). Validation runs before any write, in one transaction under
    the case lock; duplicates are refused by status and `UNIQUE (run_id)`. Document figures
    sit outside the current-value rule, and deciding which figure counts is reconciliation
    (Stage 2).
  - **Review screen (step 2-E):** Next.js 15 in `frontend/`. The browser talks only to the
    Next.js server, whose single route handler forwards an explicit allowlist of API calls,
    adding the bearer token and firm from httpOnly, Secure, SameSite=Strict cookies (no
    CORS, no token in page JavaScript). The firm is chosen from `/me`, but it is only a
    selector: the API checks membership on every request. Development-only sign-in until
    Entra is live. The original is rendered with pdf.js; evidence boxes are placed by one
    tested conversion that refuses to draw when page sizes disagree.
  - **Broker UI (Phase 2.1):** Tailwind CSS and a small set of owned components (Radix
    primitives for dialogs, tooltips and menus). The forwarder additionally allows creating
    cases and applicants (`POST cases`, `POST cases/{id}/applicants`); the API's own
    permissions decide. Display wording for API codes lives in `lib/labels.ts` and
    `lib/failures.ts`; the codes are unchanged.
  - **Reconciliation (Stage 2-A):** module `reconciliation`. Six deterministic checks
    (`payslip-checks/1`) are **pure functions** over the case's current reviewed payslip
    figures, reviewed pay dates and periods, and the declared basic salary. They run **in the
    API request** (`GET /cases/{id}/checks`); there is no worker job and no results table.
    Each finding is `passed`, `discrepancy` (both sources cited, down to the field and its
    box) or `not_run` (with the reason). Thresholds live in one versioned configuration; the
    2 % declared-basic threshold is an initial product assumption, not a regulatory
    standard. A finding's key is a SHA-256 over the rule-set version, rule, parameters and
    every cited input's identity, value and period, so any change produces a new finding.
    The only stored data is `check_acknowledgements` (insert-only for the runtime role, RLS,
    one audit event with the note in the erasable details). **Acknowledging is not resolving
    or approving:** it changes no figure and the finding stays a discrepancy.
  - **Downloads are not yet access-logged.** That is a production-readiness requirement
    before real borrower documents are enabled (retention brief R17). The platform cannot tell synthetic from
    real documents, so this is a deployment control, not content inspection.
- **PII:** never logged. Structured JSON logs carry IDs only. Field-level encryption considered for highest-risk fields (NI number, account numbers) in Phase 2+.
- **Storage:** private Blob containers, no public access, no long-lived SAS; downloads proxied through the API with authorisation and audit, or short-lived user-delegation SAS.
- **Residency:** Azure UK South (primary) / UK West (DR). Provider regions verified per service; where a model is unavailable in UK regions, this is surfaced as a decision, not silently routed elsewhere.

## 6. Technology stack (and why)

| Concern | Choice | Rationale |
|---|---|---|
| API | Python 3.13, FastAPI, Pydantic v2 | As specified; typed, OpenAPI out of the box. |
| ORM / migrations | SQLAlchemy 2.0 (async) + Alembic | De facto standard; explicit migrations. |
| DB driver | asyncpg | Async, fast, and works with the Windows Proactor event loop used in local development. (psycopg 3 was planned; its async mode needs a selector loop on Windows.) |
| DB | PostgreSQL 15+ required (`NULLS NOT DISTINCT`); 17 used locally and in Docker (Azure Flexible Server) | As specified; RLS, JSONB for raw provider output. |
| Logging | structlog (JSON) | Structured logging requirement. |
| Quality | ruff, mypy `--strict`, pytest, pytest-asyncio | Strong typing + automated tests. |
| Packaging | **uv** (needs installing) | Fast, reproducible lockfile. Alternative: pip-tools — say if you prefer. |
| Local infra | Docker Compose: Postgres, Azurite (Blob emulator, Phase 2) | Mirrors Azure services locally without cloud dependency. |
| Frontend | Next.js (App Router) + TypeScript | As specified; introduced in Phase 2 with first user-visible workflow. |
| Auth | OIDC/JWT validation behind an `AuthProvider` interface; **recommended: Microsoft Entra External ID** | Managed, Azure-aligned, UK data handling; decision needed (see Roadmap §Decisions). |
| Job queue | Decided in Phase 2 (recommendation: PostgreSQL-backed queue with `SKIP LOCKED` behind an interface; Azure Service Bus later if volume requires) | No extra infra for pilot scale; enqueue is transactional with domain writes. |
| Extraction | `DocumentExtractor` interface; Azure AI Document Intelligence adapter first | Interchangeable as required. |
| LLM | `LLMClient` interface; Azure OpenAI (UK/EU data zone) with abuse-monitoring opt-out applied for | Data protection + residency. |
| IaC | Bicep (Phase 12) | Azure-native. |
