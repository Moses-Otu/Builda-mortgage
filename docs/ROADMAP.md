# Delivery Roadmap

Revised 2026-11-19. It replaces the earlier 12-phase table: prove the core value with the
fewest document types, then the broker experience, then pilot.

**Principle:** build the MVP, not the infrastructure around it. If a simpler implementation is
secure and correct for the MVP, use it. Keep tenant isolation, access control, auditability
and data integrity. Write future needs down here instead of building them.

**Definition of success:** a broker uploads a payslip, sees correctly extracted figures with
traceable evidence, reviews and corrects them, and saves approved figures to a case.

Each step still follows: plan → approve → build → test → report → commit.

| Stage | Outcome | Status |
|---|---|---|
| Foundation | Tenancy and RLS, roles, cases, applicants, audit, financial model, hardening (Phases 1, 1.1, 1.2) | Done |
| **1. Finish Phase 2.0** | The payslip workflow end to end: **2-A** upload and storage · **2-B** background processing and safe PDF check · **2-C** classification and extraction (deterministic, synthetic test provider behind the `LayoutProvider` interface; a real provider is still to connect) · **2-D** review, correct and save to the financial model · **2-E** review screen (pdf.js with highlighted regions) · **2.1** broker UI redesign | Done |
| **2. Reconciliation engine** | **2-A (done):** six versioned payslip checks (`payslip-checks/1`): payslip arithmetic, pay-period coverage, periods paid twice, year to date (gross and tax), declared versus evidenced basic salary. Computed on request; each discrepancy cites both sources; brokers can acknowledge (not resolve or approve). **2-B: bank statements** ([plan](phases/STAGE-2B-BANK-STATEMENTS-PLAN.md)): **2B-0** tests ignore a developer `.env` (done) · **2B-1** upload, classify and extract statements with field-level provenance and keyed account fingerprints (done; local and test only) · **2B-2** broker confirmation, accounts and holders (done) · **2B-3** `bank-checks/1`: totals, running balance, gaps, and salary credits with four outcomes (done; local and test only). | Done |
| **3. Broker experience** | Review, correct and approve; trace any figure to its evidence; approve an immutable profile snapshot; generate an evidence pack. [Plan](phases/STAGE-3-BROKER-EXPERIENCE-PLAN.md): **3-A** profile and provenance (done) · **3-B** approval (versioned snapshot, `verified`) · **3-C** evidence pack | In progress |
| **4. Pilot** | Synthetic-data pilot first. Then **resolve the legal, retention and security gates before any real borrower data:** D10 retention and erasure; provider terms (training and retention exclusions); download access log (R17); storage identity without delete and account hardening (R15, R16); malware scanning; dedicated worker database role (the worker shares the API role as an MVP compromise); resource isolation for the PDF process (**no memory limit today**); Docker verification; Entra live sign-in; Docker and Azure deployment (IaC, Key Vault, managed identity, private endpoints); backup and restore drill; DPIA; penetration test. **Before any real bank statement** (refused in code until then): a DPIA covering statements, retention for statement originals, raw output and extracted transactions (R8a), the account fingerprint key in Key Vault with a rotation plan, and a real table-capable provider measured on an evaluation corpus | — |

Later, only when a pilot shows a need: more document types (P60, SA302, credit reports),
transaction categorisation, liabilities from credit reports, indicative affordability,
integrations and export, and an LLM fallback for classification.

## Decisions needed

| # | Decision | Needed by | Recommendation |
|---|---|---|---|
| D1 | Managed auth provider | Phase 1 (interface) / Phase 2 (live integration) | Microsoft Entra External ID |
| D2 | Python packaging tool | Phase 1 | uv |
| D3 | Job queue | Phase 2 | Postgres `SKIP LOCKED` queue behind interface |
| D4 | Borrower access model (portal login vs magic-link upload) | Phase 2 | Time-limited, case-scoped magic links; no borrower accounts in MVP |
| D5 | LLM model + region | Phase 3 | Azure OpenAI in UK South or EU Data Zone, subject to model availability |
| D6 | Synthetic test document corpus | Phase 3 | Generate synthetic payslips/statements; **no real customer documents in the repo** |

## Cross-cutting risks

| Risk | Impact | Mitigation |
|---|---|---|
| Credit report terms of use (statutory/consumer reports may restrict commercial reuse) | Legal | Legal review before Phase 6; explicit borrower consent capture; data processing agreement with firms |
| Regulatory drift into advice (FCA MCOB, Consumer Duty) | Regulatory | No recommendations/pass-fail; "indicative" labelling enforced in schema; broker approval required |
| UK GDPR / DPIA, data minimisation, retention | Regulatory | DPIA before pilot; per-tenant retention config; erasure workflow (Phase 12) |
| Bank statement PDF variety (layouts, scans, multi-account) | Accuracy | Layout-agnostic extraction + balance continuity checks to detect missed rows |
| Tampered documents | Fraud | Out of MVP scope for detection; preserve originals + metadata so later checks are possible; flag obvious arithmetic inconsistencies |
| Model availability in UK region | Residency | Provider interface; documented fallback decision, never silent |
| Tenant isolation bug | Severe | RLS defence in depth + dedicated isolation test suite in every phase |
| Over-trusting AI confidence | Accuracy | Status driven only by deterministic corroboration or broker action |
