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
| 3 | `applicants` | applicants, case↔applicant roles, declared data | 1 |
| 14 | `audit` | append-only event log | 1 |
| 4 | `documents` | upload, blob storage, hashing, versions, pages | 2 |
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
- **Audit:** append-only `audit_events` (DB trigger blocks UPDATE/DELETE); records actor, tenant, case, action, entity, before/after diff hash, request ID, timestamp.
- **PII:** never logged. Structured JSON logs carry IDs only. Field-level encryption considered for highest-risk fields (NI number, account numbers) in Phase 2+.
- **Storage:** private Blob containers, no public access, no long-lived SAS; downloads proxied through the API with authorisation and audit, or short-lived user-delegation SAS.
- **Residency:** Azure UK South (primary) / UK West (DR). Provider regions verified per service; where a model is unavailable in UK regions, this is surfaced as a decision, not silently routed elsewhere.

## 6. Technology stack (and why)

| Concern | Choice | Rationale |
|---|---|---|
| API | Python 3.13, FastAPI, Pydantic v2 | As specified; typed, OpenAPI out of the box. |
| ORM / migrations | SQLAlchemy 2.0 (async) + Alembic | De facto standard; explicit migrations. |
| DB driver | asyncpg | Async, fast, and works with the Windows Proactor event loop used in local development. (psycopg 3 was planned; its async mode needs a selector loop on Windows.) |
| DB | PostgreSQL 16+ (Azure Flexible Server) | As specified; RLS, JSONB for raw provider output. |
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
