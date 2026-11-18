# Delivery Roadmap

Each phase follows the same gate: inspect → plan → agree → implement → test → document. A phase is complete only when every acceptance criterion passes. Frontend work arrives in vertical slices from Phase 2 onward rather than as one late phase.

| Phase | Name | Core outcome | Key acceptance signal |
|---|---|---|---|
| **1** | Foundation & core domain | Repo, tooling, FastAPI service, Postgres + migrations, tenants/users/roles, cases, applicants, audit log, RLS tenant isolation, auth abstraction | Cross-tenant access impossible at API and DB level (tested); audit row for every mutation; CI-quality checks green |
| 2 | Document ingestion & storage | Upload API + Next.js upload/case UI, private blob storage (Azurite locally), SHA-256, immutability, page rendering, file-type/size/malware gate, async job queue | Original byte-identical on retrieval; reject non-PDF/image; job retries idempotent |
| 3 | Classification & extraction | `DocumentExtractor` + Azure DI adapter, classifier (rules first, LLM fallback), **versioned extraction schemas** for payslip, P60, SA302/tax calc, bank statement, credit report; source anchors | Golden-file tests on synthetic docs; every extracted field has page + region anchor; raw provider output stored |
| 4 | Financial fact normalisation | `FinancialFact` model, unit/period normalisation (weekly/4-weekly/monthly/annual), tax-year handling, verification status machine | Deterministic normalisation property tests; status changes only via rules or broker |
| 5 | Income & transactions | Bank transaction parsing, deterministic categorisation rules (+ LLM suggestions flagged as such), income classification incl. variable income | Salary credits matched to payslip net pay; categoriser versioned and reproducible |
| 6 | Liabilities | Credit report (uploaded) commitments, recurring outgoings (DD/SO) detection, linking report accounts to bank payments | Every liability anchored; unmatched items surfaced, not dropped |
| 7 | **Reconciliation engine** | Versioned cross-document rule set: payslip YTD continuity, P60 vs final payslip, SA302 vs tax year overview, net pay vs bank credits, declared vs evidenced income/commitments, identity consistency; discrepancies with tolerance + explanation | Each rule unit-tested with pass/fail/edge fixtures; discrepancy cites both sources |
| 8 | Rules & indicative affordability | Versioned assumption sets (income multiples, stress rate, expenditure model), broker-configurable per tenant, pure calc with stored input snapshot | Same inputs + version → identical output; labelled indicative throughout |
| 9 | Broker review & approval | Review UI: side-by-side source viewer, accept/correct/reject facts, resolve discrepancies, approve immutable profile snapshot | Corrections audited with reason; approval locks snapshot hash |
| 10 | Evidence pack | PDF + JSON pack: profile, discrepancies & resolutions, citations with page thumbnails, rule versions, approver | Every figure in pack links to page; pack hash recorded |
| 11 | Integration & export | Canonical versioned export schema, transactional outbox, signed webhooks, CRM adapter interface | Schema contract tests; at-least-once delivery with idempotency keys |
| 12 | Hardening & pilot readiness | Bicep IaC (UK South), Key Vault, private endpoints, managed identity, retention/erasure, observability, backup/restore drill, pen test, DPIA | Pilot checklist signed off |

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
